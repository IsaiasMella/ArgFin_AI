"""Consultas a los documentos para otras features (única puerta de `documents`)."""

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from brujula.features.documents.models import CnvStatement
from brujula.features.universe.models import Company


async def published_statement_periods(session: AsyncSession) -> dict[str, set[date]]:
    """Cierres con estado contable presentado a la CNV, por empresa (clave del universo)."""
    rows = await session.execute(
        select(Company.clave, CnvStatement.fecha_cierre)
        .join(Company, Company.id == CnvStatement.company_id)
        .distinct()
    )
    periods: dict[str, set[date]] = defaultdict(set)
    for clave, closing in rows:
        periods[clave].add(closing)
    return dict(periods)
