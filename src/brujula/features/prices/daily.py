"""Tarea diaria de precios y tipo de cambio CCL (T2.3, plan 7.1, ADR 012).

1. Pide los cierres de los instrumentos activos del universo y de los bonos del CCL a la
   fuente principal y a la de respaldo, en paralelo.
2. Por cada rueda guarda el dato principal y lo compara con el respaldo; si la divergencia
   supera `PRICE_DIVERGENCE_THRESHOLD_PCT`, lo marca (no se publica sin revisión).
3. Si solo una fuente tiene la rueda, guarda esa y lo registra en el log.

Idempotente: cada corrida recalcula una ventana de días y hace upsert por clave natural.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import anyio
import structlog
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import and_, func, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from brujula.core.tickers import Ticker
from brujula.features.prices.models import FxDaily, PriceDaily
from brujula.features.prices.providers import (
    AssetKind,
    DailyBar,
    FetchResult,
    PriceProvider,
    Symbol,
)
from brujula.features.prices.reconcile import Reconciled, implied_rate, reconcile
from brujula.features.universe import service as universe

logger = structlog.get_logger(__name__)

FX_FILE = "fx.yaml"
KIND_BY_COMPANY_TYPE = {"ar_equity": AssetKind.EQUITY, "cedear": AssetKind.CEDEAR}
# psycopg admite hasta 65535 parámetros por sentencia: lotes holgados.
UPSERT_BATCH = 1000


class PricesConfigError(Exception):
    """`config/fx.yaml` no existe o no cumple el esquema."""


class PricesUnavailableError(Exception):
    """Ninguna fuente devolvió datos y hubo errores: la corrida se reintenta más tarde."""


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
    ccl_guardados: int = 0
    ccl_marcados: int = 0
    # proveedor -> ticker -> motivo
    errores: dict[str, dict[str, str]] = field(default_factory=dict)


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
    if report.errores and not any(main.bars.values()) and not any(spare.bars.values()):
        raise PricesUnavailableError(f"sin datos de ninguna fuente: {report.errores}")

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

    async with session_factory() as session, session.begin():
        report.precios_cambiados = await _upsert(session, PriceDaily, price_rows, primary.name)
        await _upsert(session, FxDaily, fx_rows, primary.name)
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
