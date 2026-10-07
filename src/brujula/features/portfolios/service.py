"""Casos de uso del portafolio. Toda operación corre con el contexto RLS del usuario."""

from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from brujula.core.config import Settings
from brujula.core.db import user_transaction
from brujula.features.auth.service import AuthenticatedUser
from brujula.features.portfolios.csv_import import ParsedCsv, RowError
from brujula.features.portfolios.models import Holding
from brujula.features.portfolios.schemas import (
    Coverage,
    CsvImportResult,
    CsvRowError,
    HoldingCreate,
    HoldingOut,
    HoldingUpdate,
)
from brujula.features.universe import service as universe
from brujula.features.universe.service import InstrumentInfo

logger = structlog.get_logger(__name__)

PLAN_WITH_LIMIT = "gratis"


class PlanLimitError(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"El plan gratuito permite hasta {limit} posiciones")
        self.limit = limit


class HoldingNotFoundError(Exception):
    """No existe o es de otro usuario (RLS la hace invisible: para el usuario, no existe)."""


class PortfolioService:
    def __init__(self, settings: Settings, session_factory: async_sessionmaker[AsyncSession]):
        self._sessions = session_factory
        self._free_plan_limit = settings.free_plan_max_positions

    async def list_holdings(self, user: AuthenticatedUser) -> list[HoldingOut]:
        async with user_transaction(self._sessions, user.id) as session:
            holdings = (
                await session.scalars(select(Holding).order_by(Holding.creado_en, Holding.id))
            ).all()
            return await _to_out(session, list(holdings))

    async def add(self, user: AuthenticatedUser, data: HoldingCreate) -> HoldingOut:
        [created] = await self.add_many(user, [data])
        return created

    async def add_many(
        self, user: AuthenticatedUser, items: list[HoldingCreate]
    ) -> list[HoldingOut]:
        """Agrega todas las posiciones en una transacción, respetando el límite del plan."""
        async with user_transaction(self._sessions, user.id) as session:
            if user.plan == PLAN_WITH_LIMIT:
                current = await session.scalar(select(func.count()).select_from(Holding)) or 0
                if current + len(items) > self._free_plan_limit:
                    raise PlanLimitError(self._free_plan_limit)
            found = await universe.find_by_tickers(session, (item.ticker for item in items))
            holdings = [
                Holding(
                    user_id=user.id,
                    instrument_id=found[item.ticker].id if item.ticker in found else None,
                    ticker_libre=None if item.ticker in found else item.ticker,
                    cantidad=item.cantidad,
                    precio_promedio=item.precio_promedio,
                    moneda_precio=item.moneda_precio,
                    broker=item.broker,
                )
                for item in items
            ]
            session.add_all(holdings)
            await session.flush()
            logger.info("posiciones_agregadas", cantidad=len(holdings))
            return await _to_out(session, holdings)

    async def import_csv(self, user: AuthenticatedUser, parsed: ParsedCsv) -> CsvImportResult:
        """Guarda las filas válidas; las inválidas y las que exceden el plan se informan."""
        errors = list(parsed.errors)
        valid = parsed.valid
        slots = await self.remaining_slots(user)
        if slots is not None and len(valid) > slots:
            message = f"supera el límite del plan gratuito ({self._free_plan_limit} posiciones)"
            errors.extend(RowError(line, [message]) for line, _ in valid[slots:])
            valid = valid[:slots]
        imported = await self.add_many(user, [item for _, item in valid]) if valid else []
        return CsvImportResult(
            filas_procesadas=parsed.rows,
            importadas=imported,
            errores=[
                CsvRowError(fila=e.row, errores=e.errors)
                for e in sorted(errors, key=lambda e: e.row)
            ],
        )

    async def remaining_slots(self, user: AuthenticatedUser) -> int | None:
        """Posiciones que todavía puede cargar (None: sin límite)."""
        if user.plan != PLAN_WITH_LIMIT:
            return None
        async with user_transaction(self._sessions, user.id) as session:
            current = await session.scalar(select(func.count()).select_from(Holding)) or 0
        return max(self._free_plan_limit - current, 0)

    async def update(
        self, user: AuthenticatedUser, holding_id: UUID, data: HoldingUpdate
    ) -> HoldingOut:
        async with user_transaction(self._sessions, user.id) as session:
            holding = await session.get(Holding, holding_id)
            if holding is None:
                raise HoldingNotFoundError
            changes = data.model_dump(include=data.model_fields_set)
            if changes.get("cantidad") is None:
                changes.pop("cantidad", None)  # la cantidad no se puede borrar
            for field, value in changes.items():
                setattr(holding, field, value)
            await session.flush()
            [out] = await _to_out(session, [holding])
            return out

    async def delete(self, user: AuthenticatedUser, holding_id: UUID) -> None:
        async with user_transaction(self._sessions, user.id) as session:
            holding = await session.get(Holding, holding_id)
            if holding is None:
                raise HoldingNotFoundError
            await session.delete(holding)


async def _to_out(session: AsyncSession, holdings: list[Holding]) -> list[HoldingOut]:
    by_id = await universe.find_by_ids(
        session, (h.instrument_id for h in holdings if h.instrument_id)
    )
    # Un ticker libre puede haber entrado al universo después de cargarse (T2.1).
    by_ticker = await universe.find_by_tickers(
        session, (h.ticker_libre for h in holdings if h.ticker_libre)
    )
    result = []
    for holding in holdings:
        info: InstrumentInfo | None = (
            by_id.get(holding.instrument_id)
            if holding.instrument_id
            else by_ticker.get(holding.ticker_libre or "")
        )
        result.append(
            HoldingOut(
                id=holding.id,
                ticker=info.ticker if info else (holding.ticker_libre or ""),
                empresa=info.company if info else None,
                cobertura=Coverage.FULL if info and info.covered else Coverage.PRICE_ONLY,
                cantidad=holding.cantidad,
                precio_promedio=holding.precio_promedio,
                moneda_precio=holding.moneda_precio,
                broker=holding.broker,
            )
        )
    return result
