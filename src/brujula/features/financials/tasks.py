"""Tareas de cifras en la cola: actualización periódica del XBRL de la SEC."""

import httpx2
from procrastinate import Blueprint, RetryStrategy

from brujula.core.config import Settings, get_settings
from brujula.core.db import create_engine, create_session_factory
from brujula.core.queue import BLUEPRINTS, PERIODIC_JOBS, PeriodicJob
from brujula.features.documents.sources.sec import SecClient
from brujula.features.financials.catalog import load_metric_catalog, load_sec_mapping
from brujula.features.financials.sec_facts import SecFactsReport, update_sec_facts
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


async def sec_facts(timestamp: int) -> None:
    """La agenda Procrastinate con `timestamp` (momento programado); no se usa."""
    await refresh_sec_facts(get_settings())


def build_blueprint() -> Blueprint:
    blueprint = Blueprint()
    blueprint.task(name="sec", retry=RetryStrategy(max_attempts=3, wait=900))(sec_facts)
    return blueprint


BLUEPRINTS[NAMESPACE] = build_blueprint
PERIODIC_JOBS.append(
    PeriodicJob(
        task_name=f"{NAMESPACE}:sec",
        periodic_id="sec",
        cron=lambda settings: settings.cron_filings_check,
    )
)
