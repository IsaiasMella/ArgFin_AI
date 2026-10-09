"""Tarea diaria de precios y tipo de cambio CCL (T2.3, plan 7.1, ADR 012).

1. Pide los cierres de los instrumentos activos del universo y de los bonos del CCL a la
   fuente principal y a la de respaldo, en paralelo.
2. Por cada rueda guarda el dato principal y lo compara con el respaldo; si la divergencia
   supera `PRICE_DIVERGENCE_THRESHOLD_PCT`, lo marca (no se publica sin revisión).
3. Si solo una fuente tiene la rueda, guarda esa y lo registra en el log.
4. Si la principal reescribió cierres ya guardados (ajuste por un evento corporativo), marca
   los cierres anteriores de ese instrumento: ya no son comparables con los nuevos.

Idempotente: cada corrida recalcula una ventana de días y hace upsert por clave natural.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import anyio
import structlog
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from brujula.core.integrations import SourceRun
from brujula.core.tickers import Ticker
from brujula.features.prices.models import FxDaily, PriceDaily
from brujula.features.prices.providers import (
    AssetKind,
    DailyBar,
    FetchResult,
    PriceProvider,
    Symbol,
)
from brujula.features.prices.reconcile import (
    Reconciled,
    divergence_pct,
    implied_rate,
    reconcile,
)
from brujula.features.universe import service as universe

logger = structlog.get_logger(__name__)

FX_FILE = "fx.yaml"
KIND_BY_COMPANY_TYPE = {"ar_equity": AssetKind.EQUITY, "cedear": AssetKind.CEDEAR}
# psycopg admite hasta 65535 parámetros por sentencia: lotes holgados.
UPSERT_BATCH = 1000
# Motivos de falla de un proveedor que indican un cambio de formato de su respuesta.
FORMAT_REASONS = frozenset({"formato_inesperado", "respuesta_no_json", "valor_no_numerico"})


class PricesConfigError(Exception):
    """`config/fx.yaml` no existe o no cumple el esquema."""


class PricesUnavailableError(Exception):
    """Ninguna fuente devolvió datos y hubo errores: la corrida se reintenta más tarde."""

    def __init__(self, message: str, corridas: list[SourceRun]) -> None:
        super().__init__(message)
        self.corridas = corridas


class _CclPair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    bono_pesos: Ticker
    bono_cable: Ticker


class FxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1]
    ccl: _CclPair


def load_fx_config(path: Path) -> FxConfig:
    try:
        return FxConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except OSError as exc:
        raise PricesConfigError(f"no se pudo leer {path}: {exc.strerror}") from None
    except (yaml.YAMLError, ValidationError) as exc:
        raise PricesConfigError(f"{path} no es válido:\n{exc}") from None


@dataclass
class DailyRunReport:
    desde: date
    hasta: date
    precios_guardados: int = 0
    precios_cambiados: int = 0
    precios_marcados: int = 0
    precios_solo_respaldo: int = 0
    # Cierres anteriores a la ventana marcados porque la principal reescribió su historia.
    precios_marcados_por_ajuste: int = 0
    ccl_guardados: int = 0
    ccl_marcados: int = 0
    # proveedor -> ticker -> motivo
    errores: dict[str, dict[str, str]] = field(default_factory=dict)
    # Una por proveedor (monitor de integraciones, T3.7).
    corridas: list[SourceRun] = field(default_factory=list)


def provider_run(name: str, result: FetchResult) -> SourceRun:
    """Falla del proveedor: no trajo ninguna especie (una especie suelta no es una falla)."""
    if any(result.bars.values()) or not result.errors:
        return SourceRun(f"precios:{name}")
    reason, _ = Counter(result.errors.values()).most_common(1)[0]
    return SourceRun(f"precios:{name}", motivo=reason, formato=reason in FORMAT_REASONS)


def _by_date(bars: Iterable[DailyBar]) -> dict[date, DailyBar]:
    return {bar.fecha: bar for bar in bars}


def _merge(
    primary: dict[date, DailyBar],
    backup: dict[date, DailyBar],
    names: tuple[str, str],
    threshold_pct: Decimal,
) -> list[tuple[date, Reconciled]]:
    merged = []
    for day in sorted(primary.keys() | backup.keys()):
        result = reconcile(
            primary.get(day),
            backup.get(day),
            primary_name=names[0],
            backup_name=names[1],
            threshold_pct=threshold_pct,
        )
        if result is not None:
            merged.append((day, result))
    return merged


def _ccl_series(result: FetchResult, fx: FxConfig) -> dict[date, DailyBar]:
    pesos = _by_date(result.bars.get(fx.ccl.bono_pesos, []))
    cable = _by_date(result.bars.get(fx.ccl.bono_cable, []))
    return {day: implied_rate(pesos[day], cable[day]) for day in pesos.keys() & cable.keys()}


async def _upsert(
    session: AsyncSession,
    model: type[PriceDaily] | type[FxDaily],
    rows: Sequence[dict[str, Any]],
    primary_name: str,
) -> int:
    """Inserta o actualiza solo si algo cambió. Devuelve las filas escritas.

    Un dato guardado de la fuente principal nunca se reemplaza por uno que solo tiene el
    respaldo: si la principal falla en una corrida, el dato oficial ya guardado se conserva.
    """
    if not rows:
        return 0
    keys = [column.name for column in model.__table__.primary_key]
    written = 0
    for start in range(0, len(rows), UPSERT_BATCH):
        statement = insert(model).values(list(rows[start : start + UPSERT_BATCH]))
        values = [name for name in rows[0] if name not in keys]
        updates: dict[str, Any] = {name: statement.excluded[name] for name in values}
        statement = statement.on_conflict_do_update(
            index_elements=keys,
            set_={**updates, "actualizado_en": func.now()},
            where=and_(
                or_(
                    *(
                        model.__table__.c[name].is_distinct_from(statement.excluded[name])
                        for name in values
                    )
                ),
                or_(
                    model.__table__.c.fuente != primary_name,
                    statement.excluded.fuente == primary_name,
                ),
            ),
        )
        # RETURNING devuelve solo las filas insertadas o efectivamente actualizadas.
        result = await session.execute(statement.returning(model.__table__.c[keys[0]]))
        written += len(result.all())
    return written


async def _retroactive_adjustments(
    session: AsyncSession,
    rows: Sequence[dict[str, Any]],
    primary_name: str,
    threshold_pct: Decimal,
    start: date,
    end: date,
) -> dict[UUID, date]:
    """Instrumentos cuyos cierres oficiales ya guardados cambiaron más que el umbral.

    BYMA ajusta su serie histórica hacia atrás ante eventos corporativos (splits,
    dividendos en acciones). Devuelve, por instrumento, la primera fecha reescrita.
    """
    incoming = {
        (row["instrument_id"], row["fecha"]): row["cierre"]
        for row in rows
        if row["fuente"] == primary_name
    }
    if not incoming:
        return {}
    stored = await session.execute(
        select(PriceDaily.instrument_id, PriceDaily.fecha, PriceDaily.cierre).where(
            PriceDaily.instrument_id.in_({key[0] for key in incoming}),
            PriceDaily.fecha.between(start, end),
            PriceDaily.fuente == primary_name,
        )
    )
    adjusted: dict[UUID, date] = {}
    for instrument_id, day, close in stored:
        new = incoming.get((instrument_id, day))
        if new is not None and divergence_pct(close, new) > threshold_pct:
            adjusted[instrument_id] = min(day, adjusted.get(instrument_id, day))
    return adjusted


async def _flag_history_before(session: AsyncSession, instrument_id: UUID, start: date) -> int:
    """Marca los cierres previos a la ventana: ya no son comparables con los nuevos."""
    result = await session.execute(
        update(PriceDaily)
        .where(
            PriceDaily.instrument_id == instrument_id,
            PriceDaily.fecha < start,
            PriceDaily.marcado.is_(False),
        )
        .values(marcado=True, actualizado_en=func.now())
        .returning(PriceDaily.fecha)
    )
    return len(result.all())


async def run_daily_prices(
    session_factory: async_sessionmaker[AsyncSession],
    primary: PriceProvider,
    backup: PriceProvider,
    *,
    fx: FxConfig,
    threshold_pct: Decimal,
    start: date,
    end: date,
) -> DailyRunReport:
    async with session_factory() as session:
        instruments = await universe.active_instruments(session)
    symbols = {i.ticker: Symbol(i.ticker, KIND_BY_COMPANY_TYPE[i.tipo]) for i in instruments} | {
        t: Symbol(t, AssetKind.BOND) for t in (fx.ccl.bono_pesos, fx.ccl.bono_cable)
    }

    results: dict[str, FetchResult] = {}

    async def fetch(provider: PriceProvider) -> None:
        results[provider.name] = await provider.daily_bars(list(symbols.values()), start, end)

    async with anyio.create_task_group() as group:
        group.start_soon(fetch, primary)
        group.start_soon(fetch, backup)
    main, spare = results[primary.name], results[backup.name]
    report = DailyRunReport(desde=start, hasta=end)
    report.errores = {name: r.errors for name, r in results.items() if r.errors}
    report.corridas = [provider_run(name, result) for name, result in results.items()]
    if report.errores and not any(main.bars.values()) and not any(spare.bars.values()):
        raise PricesUnavailableError(
            f"sin datos de ninguna fuente: {report.errores}", report.corridas
        )

    names = (primary.name, backup.name)
    price_rows = []
    for instrument in instruments:
        for day, chosen in _merge(
            _by_date(main.bars.get(instrument.ticker, [])),
            _by_date(spare.bars.get(instrument.ticker, [])),
            names,
            threshold_pct,
        ):
            price_rows.append(
                {
                    "instrument_id": instrument.id,
                    "fecha": day,
                    "cierre": chosen.valor,
                    "volumen": chosen.volumen,
                    "fuente": chosen.fuente,
                    "cierre_respaldo": chosen.valor_respaldo,
                    "divergencia_pct": chosen.divergencia_pct,
                    "marcado": chosen.marcado,
                }
            )
            _log_choice(instrument.ticker, day, chosen, backup.name)
            report.precios_marcados += chosen.marcado
            report.precios_solo_respaldo += chosen.fuente == backup.name

    fx_rows = []
    for day, chosen in _merge(_ccl_series(main, fx), _ccl_series(spare, fx), names, threshold_pct):
        fx_rows.append(
            {
                "fecha": day,
                "ccl": chosen.valor,
                "fuente": chosen.fuente,
                "ccl_respaldo": chosen.valor_respaldo,
                "divergencia_pct": chosen.divergencia_pct,
                "marcado": chosen.marcado,
            }
        )
        _log_choice("CCL", day, chosen, backup.name)
        report.ccl_marcados += chosen.marcado

    tickers = {instrument.id: instrument.ticker for instrument in instruments}
    async with session_factory() as session, session.begin():
        adjusted = await _retroactive_adjustments(
            session, price_rows, primary.name, threshold_pct, start, end
        )
        report.precios_cambiados = await _upsert(session, PriceDaily, price_rows, primary.name)
        await _upsert(session, FxDaily, fx_rows, primary.name)
        for instrument_id, first_day in adjusted.items():
            flagged = await _flag_history_before(session, instrument_id, start)
            report.precios_marcados_por_ajuste += flagged
            logger.warning(
                "ajuste_retroactivo",
                ticker=tickers[instrument_id],
                desde=str(first_day),
                marcados_anteriores=flagged,
            )
    report.precios_guardados = len(price_rows)
    report.ccl_guardados = len(fx_rows)
    logger.info(
        "precios_diarios",
        desde=str(start),
        hasta=str(end),
        guardados=report.precios_guardados,
        cambiados=report.precios_cambiados,
        marcados=report.precios_marcados,
        solo_respaldo=report.precios_solo_respaldo,
        marcados_por_ajuste=report.precios_marcados_por_ajuste,
        ccl=report.ccl_guardados,
        ccl_marcados=report.ccl_marcados,
        errores=sum(len(e) for e in report.errores.values()),
    )
    return report


def _log_choice(ticker: str, day: date, chosen: Reconciled, backup_name: str) -> None:
    if chosen.marcado:
        logger.warning(
            "precio_marcado",
            ticker=ticker,
            fecha=str(day),
            divergencia_pct=str(chosen.divergencia_pct),
        )
    elif chosen.fuente == backup_name:
        logger.warning("precio_solo_respaldo", ticker=ticker, fecha=str(day))
