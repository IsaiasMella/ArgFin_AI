"""Ingesta de documentos de las empresas argentinas (T3.2, plan 7.2, ADR 014).

Por empresa, según sus fuentes en `universe.yaml`:
1. CNV: estados contables propios (el balance elegido) con sus datos estructurados y los PDF
   adjuntos configurados (estado contable, reseña informativa, memoria).
2. CNV: hechos relevantes que son comunicados de resultados.
3. SEC: 6-K cuyo texto es un comunicado de resultados.
4. Sitio de inversores: los archivos que coinciden con el patrón de la empresa.

Idempotente: cada documento se identifica por (fuente, clave externa) y se descarga una sola
vez; el archivo se guarda por hash. Una falla en una fuente no corta a las demás: queda en el
informe de la corrida (y en el monitor de integraciones, T3.7).
"""

from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import PurePosixPath
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from brujula.core.http import FetchError
from brujula.features.documents.models import CnvStatement, Document, DocumentCheck
from brujula.features.documents.settings import DocumentsConfig, DocumentType
from brujula.features.documents.sources.cnv import (
    CnvClient,
    CnvFormatError,
    Listing,
    StatementRef,
    own_statement,
    presentation_date,
)
from brujula.features.documents.sources.investor_sites import InvestorSiteClient
from brujula.features.documents.sources.sec import SecClient
from brujula.features.documents.storage import DocumentStorage
from brujula.features.documents.text import document_text, infer_period
from brujula.features.universe.catalog import CompanyEntry, Universe
from brujula.features.universe.models import Company

logger = structlog.get_logger(__name__)

CONTENT_TYPES = {".pdf": "application/pdf", ".htm": "text/html", ".html": "text/html"}
SOURCE_ERRORS = (FetchError, CnvFormatError)
# Un comunicado se publica hasta ~70 días después del cierre (el anual): se aceptan períodos
# que cerraron hasta un trimestre antes de la fecha desde la que se busca.
SITE_PERIOD_MARGIN = timedelta(days=92)


@dataclass(frozen=True)
class Candidate:
    fuente: str
    clave_externa: str
    tipo: DocumentType
    url: str
    nombre: str
    periodo: date | None
    fecha_publicacion: date | None
    fetch: Callable[[], Awaitable[bytes]]


@dataclass
class CompanyReport:
    clave: str
    nuevos: Counter[str] = field(default_factory=Counter)
    estados_estructurados: int = 0
    # fuente -> motivo (para el monitor de integraciones)
    errores: list[tuple[str, str]] = field(default_factory=list)


Step = Callable[[CompanyEntry, UUID, date, CompanyReport], Awaitable[None]]


@dataclass(frozen=True)
class Sources:
    cnv: CnvClient
    sec: SecClient
    sites: InvestorSiteClient


class Ingestor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: DocumentStorage,
        sources: Sources,
        config: DocumentsConfig,
    ) -> None:
        self._sessions = session_factory
        self._storage = storage
        self._sources = sources
        self._config = config

    async def run(
        self, universe: Universe, *, since: date | None = None, only: str | None = None
    ) -> list[CompanyReport]:
        start = since or self._config.desde
        companies = [
            c for c in universe.empresas if c.cnv is not None and (only is None or c.clave == only)
        ]
        reports = []
        for company in companies:
            report = await self.ingest_company(company, start)
            reports.append(report)
            logger.info(
                "ingesta_empresa",
                empresa=company.clave,
                nuevos=dict(report.nuevos),
                estados=report.estados_estructurados,
                errores=len(report.errores),
            )
        return reports

    async def ingest_company(self, company: CompanyEntry, since: date) -> CompanyReport:
        report = CompanyReport(clave=company.clave)
        async with self._sessions() as session:
            company_id = await session.scalar(
                select(Company.id).where(Company.clave == company.clave)
            )
        if company_id is None:
            report.errores.append(("universo", "empresa_no_sincronizada"))
            return report

        steps: list[tuple[str, Step]] = [("cnv", self._cnv_statements)]
        if "cnv_hecho_relevante" in company.comunicados:
            steps.append(("cnv_hechos", self._cnv_facts))
        if "sec_6k" in company.comunicados:
            steps.append(("sec", self._sec_releases))
        if company.inversores is not None:
            steps.append(("sitio", self._site))
        for name, step in steps:
            try:
                await step(company, company_id, since, report)
            except SOURCE_ERRORS as exc:
                reason = getattr(exc, "reason", None) or str(exc)
                report.errores.append((name, reason))
                logger.warning(
                    "fuente_con_error", empresa=company.clave, fuente=name, motivo=reason
                )
        return report

    # --- Persistencia ---------------------------------------------------------------------

    async def _known(self, fuente: str, prefix: str) -> set[str]:
        async with self._sessions() as session:
            keys = await session.scalars(
                select(Document.clave_externa).where(
                    Document.fuente == fuente, Document.clave_externa.startswith(prefix)
                )
            )
            checked = await session.scalars(
                select(DocumentCheck.clave_externa).where(
                    DocumentCheck.fuente == fuente, DocumentCheck.clave_externa.startswith(prefix)
                )
            )
            return set(keys) | set(checked)

    async def _store(
        self, session: AsyncSession, company_id: UUID, candidate: Candidate, content: bytes
    ) -> Document:
        extension = PurePosixPath(candidate.nombre).suffix.lower() or ".bin"
        stored = await self._storage.save(content, extension)
        document = Document(
            company_id=company_id,
            tipo=candidate.tipo,
            fuente=candidate.fuente,
            clave_externa=candidate.clave_externa,
            periodo=candidate.periodo,
            url_origen=candidate.url,
            nombre_archivo=candidate.nombre,
            tipo_contenido=CONTENT_TYPES.get(extension, "application/octet-stream"),
            hash_sha256=stored.sha256,
            tamano_bytes=stored.size,
            ruta_almacenada=stored.path,
            fecha_publicacion=candidate.fecha_publicacion,
        )
        session.add(document)
        await session.flush()
        return document

    async def _save_candidates(
        self, company_id: UUID, candidates: list[Candidate], report: CompanyReport
    ) -> None:
        for candidate in candidates:
            content = await candidate.fetch()
            async with self._sessions() as session, session.begin():
                await self._store(session, company_id, candidate, content)
            report.nuevos[candidate.tipo] += 1

    async def _mark_checked(self, fuente: str, clave: str, resultado: str) -> None:
        async with self._sessions() as session, session.begin():
            session.add(DocumentCheck(fuente=fuente, clave_externa=clave, resultado=resultado))

    # --- CNV ------------------------------------------------------------------------------

    async def _cnv_statements(
        self, company: CompanyEntry, company_id: UUID, since: date, report: CompanyReport
    ) -> None:
        if company.cnv is None:
            return
        cnv = self._sources.cnv
        listings = await cnv.listings(company.cnv.cuit, "INFOFI", since)
        refs = [
            ref
            for ref in (own_statement(listing) for listing in listings)
            if ref is not None and ref.tipo_balance == company.cnv.balance
        ]
        # Los adjuntos se descargan antes de guardar el estado: si existe, está completo.
        async with self._sessions() as session:
            done = set(
                await session.scalars(
                    select(CnvStatement.presentacion_id).where(
                        CnvStatement.company_id == company_id
                    )
                )
            )
        for ref in refs:
            if ref.listing.presentacion_id not in done:
                await self._cnv_statement(company_id, ref, report)

    async def _cnv_statement(
        self, company_id: UUID, ref: StatementRef, report: CompanyReport
    ) -> None:
        cnv = self._sources.cnv
        presentation = await cnv.presentation(ref.listing)
        listing = ref.listing
        attachments = [
            (attachment, self._config.cnv.adjuntos[attachment.propiedad])
            for attachment in presentation.adjuntos
            if attachment.propiedad in self._config.cnv.adjuntos
            and not self._config.cnv.ignores(attachment.nombre)
        ]
        downloads = [(a, tipo, await cnv.download(a.guid)) for a, tipo in attachments]

        props = presentation.propiedades
        async with self._sessions() as session, session.begin():
            known = set(
                await session.scalars(
                    select(Document.clave_externa).where(
                        Document.fuente == "cnv",
                        Document.clave_externa.startswith(f"cnv:{listing.presentacion_id}:"),
                    )
                )
            )
            # Algunas empresas suben también una nota de presentación como "estado contable":
            # el balance es el PDF más grande.
            statement_document: UUID | None = None
            statement_size = -1
            for attachment, tipo, content in downloads:
                key = f"cnv:{listing.presentacion_id}:{attachment.guid}"
                if key in known:
                    continue
                document = await self._store(
                    session,
                    company_id,
                    Candidate(
                        fuente="cnv",
                        clave_externa=key,
                        tipo=tipo,
                        url=listing.view_url,
                        nombre=attachment.nombre,
                        periodo=ref.fecha_cierre,
                        fecha_publicacion=listing.fecha,
                        fetch=_never,
                    ),
                    content,
                )
                report.nuevos[tipo] += 1
                if tipo == "estado_contable" and document.tamano_bytes > statement_size:
                    statement_document, statement_size = document.id, document.tamano_bytes
            existing = await session.get(CnvStatement, listing.presentacion_id)
            if existing is None:
                session.add(
                    CnvStatement(
                        presentacion_id=listing.presentacion_id,
                        company_id=company_id,
                        fecha_cierre=presentation_date(props.get("FechaCierre"))
                        or ref.fecha_cierre,
                        periodicidad=ref.periodicidad,
                        tipo_balance=ref.tipo_balance,
                        moneda=props.get("Moneda"),
                        unidad=props.get("UnidadMedida"),
                        norma_contable=props.get("NormasContablesAplicadas"),
                        cuentas=presentation.cuentas,
                        url_origen=listing.view_url,
                        fecha_publicacion=listing.fecha,
                        document_id=statement_document,
                    )
                )
                report.estados_estructurados += 1
            elif statement_document is not None:
                existing.document_id = statement_document

    async def _cnv_facts(
        self, company: CompanyEntry, company_id: UUID, since: date, report: CompanyReport
    ) -> None:
        if company.cnv is None:
            return
        cnv = self._sources.cnv
        listings = [
            listing
            for listing in await cnv.listings(company.cnv.cuit, "HECHOR", since)
            if self._config.cnv.is_results_fact(listing.descripcion)
        ]
        for listing in listings:
            prefix = f"cnv:{listing.presentacion_id}:"
            if await self._known("cnv", prefix):
                continue
            presentation = await cnv.presentation(listing)
            candidates = [
                _cnv_candidate(cnv, listing, a.guid, a.nombre)
                for a in presentation.adjuntos
                if not self._config.cnv.ignores(a.nombre)
            ]
            if candidates:
                await self._save_candidates(company_id, candidates, report)
            else:
                await self._mark_checked("cnv", f"{prefix}sin_adjuntos", "sin_adjuntos")

    # --- SEC ------------------------------------------------------------------------------

    async def _sec_releases(
        self, company: CompanyEntry, company_id: UUID, since: date, report: CompanyReport
    ) -> None:
        if company.cik_sec is None:
            return
        sec, cik = self._sources.sec, company.cik_sec
        extensions = tuple(self._config.sec.extensiones)
        for filing in await sec.filings(cik, "6-K", since):
            prefix = f"sec:{filing.accession}:"
            if await self._known("sec", prefix):
                continue
            names = [
                n
                for n in await sec.files(cik, filing.accession)
                if n.lower().endswith(extensions) and "index" not in n.lower()
            ]
            matched: list[Candidate] = []
            for name in names:
                content = await sec.download(cik, filing.accession, name)
                text = document_text(content, name, self._config.sec.caracteres_a_revisar)
                if self._config.sec.is_results_release(text):
                    matched.append(
                        Candidate(
                            fuente="sec",
                            clave_externa=f"{prefix}{name}",
                            tipo="comunicado_resultados",
                            url=sec.file_url(cik, filing.accession, name),
                            nombre=name,
                            periodo=infer_period(text),
                            fecha_publicacion=filing.filing_date,
                            fetch=_constant(content),
                        )
                    )
            if matched:
                await self._save_candidates(company_id, matched, report)
            else:
                await self._mark_checked("sec", f"{prefix}*", "no_es_comunicado")

    # --- Sitio de inversores --------------------------------------------------------------

    async def _site(
        self, company: CompanyEntry, company_id: UUID, since: date, report: CompanyReport
    ) -> None:
        if company.inversores is None:
            return
        sites = self._sources.sites
        known = await self._known("sitio_inversores", "web:")
        candidates = []
        # Las páginas listan comunicados de años anteriores: solo los de períodos recientes.
        oldest_period = since - SITE_PERIOD_MARGIN
        for document in await sites.documents(company.inversores, since):
            key = f"web:{document.url}"
            period = infer_period(document.nombre)
            if key in known or (period is not None and period < oldest_period):
                continue
            candidates.append(
                Candidate(
                    fuente="sitio_inversores",
                    clave_externa=key,
                    tipo="comunicado_resultados",
                    url=document.url,
                    nombre=document.nombre,
                    periodo=period,
                    fecha_publicacion=document.fecha,
                    fetch=_downloader(sites, document.url),
                )
            )
        await self._save_candidates(company_id, candidates, report)


def _cnv_candidate(cnv: CnvClient, listing: Listing, guid: str, name: str) -> Candidate:
    async def download() -> bytes:
        return await cnv.download(guid)

    return Candidate(
        fuente="cnv",
        clave_externa=f"cnv:{listing.presentacion_id}:{guid}",
        tipo="comunicado_resultados",
        url=listing.view_url,
        nombre=name,
        periodo=infer_period(listing.descripcion) or infer_period(name),
        fecha_publicacion=listing.fecha,
        fetch=download,
    )


def _downloader(sites: InvestorSiteClient, url: str) -> Callable[[], Awaitable[bytes]]:
    async def download() -> bytes:
        return await sites.download(url)

    return download


def _constant(content: bytes) -> Callable[[], Awaitable[bytes]]:
    async def value() -> bytes:
        return content

    return value


async def _never() -> bytes:  # el contenido ya se descargó antes de abrir la transacción
    raise AssertionError("no se usa")
