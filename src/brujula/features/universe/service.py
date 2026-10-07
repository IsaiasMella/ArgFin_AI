"""Consultas al universo para otras features (única puerta de `universe`)."""

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Row, Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from brujula.features.universe.models import Company, Instrument


@dataclass(frozen=True)
class InstrumentInfo:
    id: UUID
    ticker: str
    company: str
    # Cobertura completa (informe profundo) solo si el instrumento y su empresa siguen en el
    # universo (config/universe.yaml).
    covered: bool


def _base_query() -> Select[UUID, str, str, bool]:
    return select(
        Instrument.id,
        Instrument.ticker_byma,
        Company.nombre,
        and_(Company.activa, Instrument.activo),
    ).join(Company, Company.id == Instrument.company_id)


def _info(row: Row[UUID, str, str, bool]) -> InstrumentInfo:
    instrument_id, ticker, company, active = row
    return InstrumentInfo(id=instrument_id, ticker=ticker, company=company, covered=active)


async def find_by_tickers(
    session: AsyncSession, tickers: Iterable[str]
) -> dict[str, InstrumentInfo]:
    """Instrumentos del universo para esos tickers BYMA; los que faltan no están cubiertos."""
    wanted = {ticker.upper() for ticker in tickers}
    if not wanted:
        return {}
    rows = await session.execute(_base_query().where(Instrument.ticker_byma.in_(wanted)))
    return {info.ticker: info for info in (_info(row) for row in rows)}


async def find_by_ids(session: AsyncSession, ids: Iterable[UUID]) -> dict[UUID, InstrumentInfo]:
    wanted = set(ids)
    if not wanted:
        return {}
    rows = await session.execute(_base_query().where(Instrument.id.in_(wanted)))
    return {info.id: info for info in (_info(row) for row in rows)}
