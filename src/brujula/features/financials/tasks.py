"""Tareas de cifras en la cola: actualización periódica del XBRL de la SEC."""

import httpx2
from procrastinate import Blueprint, RetryStrategy
from sqlalchemy.ext.asyncio import AsyncEngine

from brujula.core.config import Settings, get_settings
from brujula.core.db import create_engine, create_session_factory
from brujula.core.llm.client import create_llm_client
from brujula.core.llm.prompts import PromptRegistry
from brujula.core.queue import BLUEPRINTS, PERIODIC_JOBS, PeriodicJob
from brujula.features.documents.sources.sec import SecClient
from brujula.features.documents.storage import DiskStorage
from brujula.features.financials.catalog import (
    load_metric_catalog,
    load_sec_mapping,
    load_sector_metrics,
)
from brujula.features.financials.cnv_mapping import load_cnv_accounts
from brujula.features.financials.llm_extractor import LLMStatementExtractor, StatementExtractor
from brujula.features.financials.pipeline import StatementVerifier, VerificationReport
from brujula.features.financials.sec_facts import SecFactsReport, update_sec_facts
from brujula.features.financials.verification import load_extraction_config
from brujula.features.universe.catalog import UNIVERSE_FILE, load_universe

NAMESPACE = "cifras"
# companyfacts de una empresa grande pesa varios MB.
TIMEOUT_SECONDS = 120.0


async def refresh_sec_facts(
    settings: Settings,
    *,
    only: str | None = None,
    http_transport: httpx2.AsyncBaseTransport | None = None,
) -> list[SecFactsReport]:
    universe = load_universe(settings.config_dir / UNIVERSE_FILE)
    catalog = load_metric_catalog(settings.config_dir)
    mapping = load_sec_mapping(settings.config_dir, catalog)
    engine = create_engine(settings)
    try:
        async with httpx2.AsyncClient(transport=http_transport, timeout=TIMEOUT_SECONDS) as http:
            return await update_sec_facts(
                create_session_factory(engine),
                SecClient(settings, http),
                universe,
                mapping,
                catalog,
                only=only,
            )
    finally:
        await engine.dispose()


def build_statement_verifier(
    settings: Settings,
    engine: AsyncEngine,
    *,
    model: str | None = None,
    extractor: StatementExtractor | None = None,
) -> StatementVerifier:
    """El verificador con su extractor LLM (`model` reemplaza a `LLM_EXTRACTION_MODEL`)."""
    universe = load_universe(settings.config_dir / UNIVERSE_FILE)
    catalog = load_metric_catalog(settings.config_dir)
    config = load_extraction_config(settings.config_dir)
    if extractor is None:
        extractor = LLMStatementExtractor(
            create_llm_client(settings, engine),
            PromptRegistry(settings.prompts_dir).get(config.prompt),
            catalog,
            model=model,
        )
    return StatementVerifier(
        create_session_factory(engine),
        DiskStorage(settings.document_storage_dir),
        extractor,
        universe=universe,
        catalog=catalog,
        accounts=load_cnv_accounts(settings.config_dir, catalog),
        sectors=load_sector_metrics(
            settings.config_dir, catalog, {c.sector for c in universe.empresas}
        ),
        config=config,
    )


async def verify_statements(
    settings: Settings,
    *,
    only: str | None = None,
    limit: int | None = None,
    model: str | None = None,
) -> list[VerificationReport]:
    """Verificación triple de los estados contables pendientes (T3.5)."""
    engine = create_engine(settings)
    try:
        verifier = build_statement_verifier(settings, engine, model=model)
        return await verifier.run(only=only, limit=limit)
    finally:
        await engine.dispose()


async def sec_facts(timestamp: int) -> None:
    """La agenda Procrastinate con `timestamp` (momento programado); no se usa."""
    await refresh_sec_facts(get_settings())


async def verify_pending(timestamp: int) -> None:
    """La agenda Procrastinate con `timestamp` (momento programado); no se usa."""
    await verify_statements(get_settings())


def build_blueprint() -> Blueprint:
    blueprint = Blueprint()
    blueprint.task(name="sec", retry=RetryStrategy(max_attempts=3, wait=900))(sec_facts)
    blueprint.task(name="verificar", retry=RetryStrategy(max_attempts=3, wait=900))(verify_pending)
    return blueprint


BLUEPRINTS[NAMESPACE] = build_blueprint
PERIODIC_JOBS.append(
    PeriodicJob(
        task_name=f"{NAMESPACE}:sec",
        periodic_id="sec",
        cron=lambda settings: settings.cron_filings_check,
    )
)
PERIODIC_JOBS.append(
    PeriodicJob(
        task_name=f"{NAMESPACE}:verificar",
        periodic_id="verificar",
        cron=lambda settings: settings.cron_filings_check,
    )
)
