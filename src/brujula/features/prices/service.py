"""Lectura de precios y CCL para otras features (única puerta de `prices`).

Los informes leen precios solo por acá: los datos marcados por divergencia quedan afuera
hasta que alguien los revise (plan 7.1).
"""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brujula.features.prices.models import FxDaily, PriceDaily


async def publishable_closes(
    session: AsyncSession, instrument_ids: Iterable[UUID], start: date, end: date
) -> dict[UUID, dict[date, Decimal]]:
    """Cierres publicables (no marcados) por instrumento y fecha, entre `start` y `end`."""
    wanted = set(instrument_ids)
    if not wanted:
        return {}
    rows = await session.execute(
        select(PriceDaily.instrument_id, PriceDaily.fecha, PriceDaily.cierre).where(
            PriceDaily.instrument_id.in_(wanted),
            PriceDaily.fecha.between(start, end),
            PriceDaily.marcado.is_(False),
        )
    )
    closes: dict[UUID, dict[date, Decimal]] = {}
    for instrument_id, day, close in rows:
        closes.setdefault(instrument_id, {})[day] = close
    return closes


async def publishable_ccl(session: AsyncSession, start: date, end: date) -> dict[date, Decimal]:
    """CCL publicable (no marcado) por fecha, entre `start` y `end`."""
    rows = await session.execute(
        select(FxDaily.fecha, FxDaily.ccl).where(
            FxDaily.fecha.between(start, end), FxDaily.marcado.is_(False)
        )
    )
    return {day: ccl for day, ccl in rows}
