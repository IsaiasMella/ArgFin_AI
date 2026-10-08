"""Escritura idempotente de cifras en `financial_facts` (compartida por todas las fuentes)."""

from typing import Any

from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from brujula.features.financials.models import FinancialFact

UPSERT_BATCH = 500
UPDATED_COLUMNS = (
    "document_id",
    "valor",
    "moneda",
    "unidad",
    "base_medicion",
    "fecha_reexpresion",
    "referencia",
    "pagina",
    "formulario",
    "fecha_presentacion",
    "extractor_version",
)


async def upsert_facts(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Inserta o actualiza por (empresa, métrica, período, fuente), solo si algo cambió.
    Devuelve las filas escritas."""
    if not rows:
        return 0
    written = 0
    table = FinancialFact.__table__
    for start in range(0, len(rows), UPSERT_BATCH):
        batch = [
            {name: None for name in UPDATED_COLUMNS} | row
            for row in rows[start : start + UPSERT_BATCH]
        ]
        insert_rows = insert(FinancialFact).values(batch)
        statement = insert_rows.on_conflict_do_update(
            constraint="uq_financial_facts_metrica_periodo_fuente",
            set_={name: insert_rows.excluded[name] for name in UPDATED_COLUMNS},
            where=or_(
                *(
                    table.c[name].is_distinct_from(insert_rows.excluded[name])
                    for name in UPDATED_COLUMNS
                )
            ),
        ).returning(table.c.id)
        written += len((await session.execute(statement)).all())
    return written
