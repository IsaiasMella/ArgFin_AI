"""Tareas de documentos en la cola: la búsqueda periódica (`CRON_FILINGS_CHECK`)."""

from datetime import date

import httpx2
from procrastinate import Blueprint, RetryStrategy

from brujula.core.config import Settings, get_settings
from brujula.core.db import create_engine, create_session_factory
from brujula.core.queue import BLUEPRINTS, PERIODIC_JOBS, PeriodicJob
from brujula.features.documents.ingest import CompanyReport, Ingestor, Sources
from brujula.features.documents.settings import DOCUMENTS_FILE, load_documents_config
from brujula.features.documents.sources.cnv import CnvClient
from brujula.features.documents.sources.investor_sites import InvestorSiteClient
from brujula.features.documents.sources.sec import SecClient
from brujula.features.documents.storage import DiskStorage
from brujula.features.universe.catalog import UNIVERSE_FILE, load_universe

NAMESPACE = "documentos"
# Los PDF de estados contables pesan varios MB y la CNV a veces es lenta.
TIMEOUT_SECONDS = 120.0


async def ingest_documents(
    settings: Settings,
    *,
    since: date | None = None,
    only: str | None = None,
    http_transport: httpx2.AsyncBaseTransport | None = None,
) -> list[CompanyReport]:
    """Arma las dependencias desde la configuración y corre la ingesta."""
    universe = load_universe(settings.config_dir / UNIVERSE_FILE)
    config = load_documents_config(settings.config_dir / DOCUMENTS_FILE)
    engine = create_engine(settings)
    try:
        async with httpx2.AsyncClient(transport=http_transport, timeout=TIMEOUT_SECONDS) as http:
            sources = Sources(
                cnv=CnvClient(settings, http),
                sec=SecClient(settings, http),
                sites=InvestorSiteClient(http),
            )
            ingestor = Ingestor(
                create_session_factory(engine),
                DiskStorage(settings.document_storage_dir),
                sources,
                config,
            )
            return await ingestor.run(universe, since=since, only=only)
    finally:
        await engine.dispose()


async def check_filings(timestamp: int) -> None:
    """La agenda Procrastinate con `timestamp` (momento programado); no se usa."""
    await ingest_documents(get_settings())


def build_blueprint() -> Blueprint:
    blueprint = Blueprint()
    # Idempotente: si algo falla por fuera de las fuentes (p. ej. la base), se reintenta.
    blueprint.task(name="buscar", retry=RetryStrategy(max_attempts=3, wait=900))(check_filings)
    return blueprint


BLUEPRINTS[NAMESPACE] = build_blueprint
PERIODIC_JOBS.append(
    PeriodicJob(
        task_name=f"{NAMESPACE}:buscar",
        periodic_id="periodica",
        cron=lambda settings: settings.cron_filings_check,
    )
)
