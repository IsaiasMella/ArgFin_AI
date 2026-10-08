"""Verificación triple de los estados contables descargados (T3.5, ADR 017).

Por cada estado contable pendiente (la última presentación de cada período):
1. Mapea los datos estructurados de la CNV a métricas (T3.4).
2. Lee el PDF firmado y le pide al LLM una extracción independiente de las mismas métricas.
3. Verifica (`verification.verify`): literal en el PDF, LLM y validaciones contables.
4. Si todo coincide: publica las cifras en `financial_facts` y el documento queda
   `validado`. Si no: guarda las diferencias y el documento queda en `revision_manual`.

Una falla del LLM (presupuesto agotado, proveedor caído) no es una diferencia: el documento
sigue `descargado` y se reintenta en la próxima corrida.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import partial
from typing import Any
from uuid import UUID

import anyio
import pymupdf
import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from brujula.core.llm.client import LLMError
from brujula.features.documents.models import CnvStatement, Document
from brujula.features.documents.storage import DocumentStorage
from brujula.features.documents.text import ocr_pages, pdf_pages
from brujula.features.financials.catalog import MetricCatalog, SectorMetrics
from brujula.features.financials.cnv_mapping import (
    AmountError,
    CnvAccountMap,
    fiscal_year_start,
    map_statement,
    unit_multiplier,
)
from brujula.features.financials.llm_extractor import PagePayload, StatementExtractor
from brujula.features.financials.models import FinancialFact, VerificationIssue
from brujula.features.financials.store import upsert_facts
from brujula.features.financials.verification import (
    ExtractionConfig,
    Issue,
    StatementContext,
    pages_for_llm,
    verify,
)
from brujula.features.universe.catalog import Universe
from brujula.features.universe.models import Company

logger = structlog.get_logger(__name__)

EXTRACTOR_VERSION = "cnv@1"
SCAN_DPI = 150


@dataclass(frozen=True)
class PendingStatement:
    statement: CnvStatement
    document: Document
    clave: str
    sector: str


@dataclass
class VerificationReport:
    clave: str
    fecha_cierre: date
    documento: UUID
    estado: str = "descargado"
    diferencias: list[Issue] = field(default_factory=list)
    costo_usd: Decimal = Decimal(0)
    error: str | None = None


def _page_images(content: bytes, numbers: Sequence[int]) -> dict[int, bytes]:
    with pymupdf.open(stream=content, filetype="pdf") as document:  # type: ignore[no-untyped-call]
        return {
            number: document[number - 1].get_pixmap(dpi=SCAN_DPI).tobytes("png")
            for number in numbers
        }


class StatementVerifier:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: DocumentStorage,
        extractor: StatementExtractor,
        *,
        universe: Universe,
        catalog: MetricCatalog,
        accounts: CnvAccountMap,
        sectors: SectorMetrics,
        config: ExtractionConfig,
    ) -> None:
        self._sessions = session_factory
        self._storage = storage
        self._extractor = extractor
        self._fiscal_ends = {c.clave: c.cnv.cierre_ejercicio for c in universe.empresas if c.cnv}
        self._catalog = catalog
        self._accounts = accounts
        self._sectors = sectors
        self._config = config

    async def pending(self, *, only: str | None = None) -> list[PendingStatement]:
        """La última presentación de cada período con su PDF aún sin verificar."""
        latest = (
            select(CnvStatement)
            .ext(
                distinct_on(
                    CnvStatement.company_id, CnvStatement.fecha_cierre, CnvStatement.tipo_balance
                )
            )
            .order_by(
                CnvStatement.company_id,
                CnvStatement.fecha_cierre,
                CnvStatement.tipo_balance,
                CnvStatement.presentacion_id.desc(),
            )
            .subquery()
        )
        query = (
            select(CnvStatement, Document, Company.clave, Company.sector)
            .join(latest, latest.c.presentacion_id == CnvStatement.presentacion_id)
            .join(Document, Document.id == CnvStatement.document_id)
            .join(Company, Company.id == CnvStatement.company_id)
            .where(Document.estado == "descargado")
            .order_by(Company.clave, CnvStatement.fecha_cierre)
        )
        if only is not None:
            query = query.where(Company.clave == only)
        async with self._sessions() as session:
            rows = (await session.execute(query)).all()
        return [PendingStatement(s, d, clave, sector) for s, d, clave, sector in rows]

    async def run(
        self, *, only: str | None = None, limit: int | None = None
    ) -> list[VerificationReport]:
        reports = []
        for item in (await self.pending(only=only))[:limit]:
            report = await self.verify_one(item)
            reports.append(report)
            logger.info(
                "verificacion_estado",
                empresa=report.clave,
                cierre=str(report.fecha_cierre),
                estado=report.estado,
                diferencias=len(report.diferencias),
                costo_usd=str(report.costo_usd),
                error=report.error,
            )
        return reports

    async def _previous_balances(self, company_id: UUID, before: date) -> dict[str, Decimal]:
        saldos = [code for code, m in self._catalog.metricas.items() if m.tipo == "saldo"]
        async with self._sessions() as session:
            rows = await session.execute(
                select(FinancialFact.metrica, FinancialFact.valor)
                .where(
                    FinancialFact.company_id == company_id,
                    FinancialFact.fuente == "cnv",
                    FinancialFact.metrica.in_(saldos),
                    FinancialFact.periodo_fin < before,
                )
                .order_by(FinancialFact.metrica, FinancialFact.periodo_fin.desc())
                .ext(distinct_on(FinancialFact.metrica))
            )
            return {metric: value for metric, value in rows}

    async def verify_one(self, item: PendingStatement) -> VerificationReport:
        statement, document = item.statement, item.document
        report = VerificationReport(item.clave, statement.fecha_cierre, document.id)
        content = await self._storage.read(document.ruta_almacenada)
        pages = pdf_pages(content)
        # Páginas escaneadas (sin texto): se leen con OCR y además van como imagen al LLM.
        ocr = self._config.ocr
        scanned = [
            number
            for number, text in enumerate(pages[: ocr.max_paginas], start=1)
            if len(text.strip()) < self._config.min_caracteres_texto
        ]
        if scanned:
            texts = await anyio.to_thread.run_sync(
                partial(ocr_pages, content, scanned, language=ocr.idioma, dpi=ocr.dpi)
            )
            pages = [texts.get(number, text) for number, text in enumerate(pages, start=1)]
        fiscal_end = self._fiscal_ends.get(item.clave, "12-31")
        mapped = map_statement(
            statement.cuentas,
            unidad=statement.unidad,
            fecha_cierre=statement.fecha_cierre,
            cierre_ejercicio=fiscal_end,
            accounts=self._accounts,
            catalog=self._catalog,
        )
        try:
            unit = unit_multiplier(statement.unidad)
        except AmountError:
            unit = Decimal(1)  # el mapeo ya lo informó como diferencia
        context = StatementContext(
            fecha_cierre=statement.fecha_cierre,
            unidad_cnv=unit,
            moneda_cnv=statement.moneda,
            required=self._sectors.required(item.sector),
            previous=await self._previous_balances(statement.company_id, statement.fecha_cierre),
        )
        selected = pages_for_llm(pages, mapped.facts, context, self._catalog, self._config)
        scanned = [
            n for n in selected if len(pages[n - 1].strip()) < self._config.min_caracteres_texto
        ]
        images = _page_images(content, scanned) if scanned else {}
        payload = [PagePayload(n, pages[n - 1], images.get(n)) for n in selected]
        try:
            outcome = await self._extractor.extract(
                pages=payload,
                metrics=[fact.metrica for fact in mapped.facts],
                fecha_cierre=statement.fecha_cierre,
                inicio_ejercicio=fiscal_year_start(statement.fecha_cierre, fiscal_end),
                tipo_balance=statement.tipo_balance,
            )
        except LLMError as exc:
            report.error = f"{type(exc).__name__}: {exc}"
            return report
        result = verify(
            mapped.facts,
            mapped.errores,
            pages,
            outcome.extraction,
            context,
            self._catalog,
            self._config,
        )
        report.costo_usd = outcome.cost_usd
        report.diferencias = result.issues
        report.estado = "validado" if result.ok else "revision_manual"
        currency = self._config.monedas_cnv.get(statement.moneda or "")
        rows: list[dict[str, Any]] = [
            {
                "company_id": statement.company_id,
                "document_id": document.id,
                "metrica": verified.fact.metrica,
                "periodo_inicio": verified.fact.periodo_inicio,
                "periodo_fin": verified.fact.periodo_fin,
                "valor": verified.fact.valor,
                "moneda": currency if verified.fact.unidad != "acciones" else None,
                "unidad": verified.fact.unidad,
                "base_medicion": "homogenea",
                "fecha_reexpresion": statement.fecha_cierre,
                "fuente": "cnv",
                "referencia": (
                    f"presentación {statement.presentacion_id},"
                    f" cuenta {'+'.join(verified.fact.cuentas)}"
                ),
                "pagina": verified.pagina,
                "formulario": "cnv_aif",
                "fecha_presentacion": statement.fecha_publicacion,
                "extractor_version": f"{EXTRACTOR_VERSION}+{outcome.model}",
            }
            for verified in result.verified
        ]
        async with self._sessions() as session, session.begin():
            await session.execute(
                delete(VerificationIssue).where(VerificationIssue.document_id == document.id)
            )
            if result.ok:
                await upsert_facts(session, rows)
            session.add_all(
                VerificationIssue(
                    document_id=document.id,
                    metrica=issue.metrica,
                    motivo=issue.motivo,
                    detalle=issue.detalle,
                    valor_cnv=issue.valor_cnv,
                    valor_llm=issue.valor_llm,
                    pagina=issue.pagina,
                    bloqueante=issue.bloqueante,
                    modelo=outcome.model,
                )
                for issue in result.issues
            )
            stored = await session.get(Document, document.id)
            if stored is not None:
                stored.estado = report.estado
                stored.verificado_en = datetime.now(UTC)
                stored.costo_verificacion_usd = outcome.cost_usd
        return report
