"""Actualiza las cifras XBRL de la SEC de los subyacentes de CEDEARs (T3.3, ADR 015).

Idempotente: upsert por (empresa, métrica, período, fuente) que solo escribe si el valor o
su origen cambiaron (por ejemplo, una reexpresión en una presentación posterior).
"""

from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from brujula.core.http import FetchError
from brujula.features.documents.sources.sec import SecClient
from brujula.features.financials.catalog import MetricCatalog, SecXbrlMapping
from brujula.features.financials.store import upsert_facts
from brujula.features.financials.xbrl import XbrlFact, extract_facts
from brujula.features.universe.catalog import Universe
from brujula.features.universe.models import Company

logger = structlog.get_logger(__name__)

EXTRACTOR_VERSION = "sec_xbrl@1"


@dataclass
class SecFactsReport:
    clave: str
    cifras: int = 0
    escritas: int = 0
    errores: list[tuple[str, str]] = field(default_factory=list)


def _row(company_id: Any, fact: XbrlFact) -> dict[str, Any]:
    return {
        "company_id": company_id,
        "metrica": fact.metrica,
        "periodo_inicio": fact.periodo_inicio,
        "periodo_fin": fact.periodo_fin,
        "valor": fact.valor,
        "moneda": fact.moneda,
        "unidad": fact.unidad,
        "base_medicion": "nominal",
        "fuente": "sec_xbrl",
        "referencia": f"{fact.concepto} {fact.accession}",
        "formulario": fact.formulario,
        "fecha_presentacion": fact.fecha_presentacion,
        "extractor_version": EXTRACTOR_VERSION,
    }


async def update_sec_facts(
    session_factory: async_sessionmaker[AsyncSession],
    sec: SecClient,
    universe: Universe,
    mapping: SecXbrlMapping,
    catalog: MetricCatalog,
    *,
    only: str | None = None,
) -> list[SecFactsReport]:
    companies = [
        c
        for c in universe.empresas
        if c.tipo == "cedear" and c.cik_sec and (only is None or c.clave == only)
    ]
    reports = []
    for company in companies:
        report = SecFactsReport(clave=company.clave)
        reports.append(report)
        async with session_factory() as session:
            company_id = await session.scalar(
                select(Company.id).where(Company.clave == company.clave)
            )
        if company_id is None or company.cik_sec is None:
            report.errores.append(("universo", "empresa_no_sincronizada"))
            continue
        try:
            data = await sec.company_facts(company.cik_sec)
        except FetchError as exc:
            report.errores.append(("sec_xbrl", exc.reason))
            logger.warning("sec_xbrl_error", empresa=company.clave, motivo=exc.reason)
            continue
        facts = extract_facts(data, mapping, catalog)
        report.cifras = len(facts)
        async with session_factory() as session, session.begin():
            report.escritas = await upsert_facts(session, [_row(company_id, f) for f in facts])
        logger.info(
            "sec_xbrl_empresa",
            empresa=company.clave,
            cifras=report.cifras,
            escritas=report.escritas,
        )
    return reports
