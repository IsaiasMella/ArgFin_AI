"""Punto de entrada del monitor para las tareas de cada integración (T3.7)."""

from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx2
import structlog

from brujula.core.config import Settings
from brujula.core.db import create_engine, create_session_factory
from brujula.core.email import ResendEmailSender
from brujula.core.integrations import SourceRun
from brujula.features.documents.service import published_statement_periods
from brujula.features.integrations.service import IntegrationMonitor, load_monitor_config
from brujula.features.universe.catalog import UNIVERSE_FILE, load_universe

logger = structlog.get_logger(__name__)

TIMEOUT_SECONDS = 30.0


async def report_runs(
    settings: Settings,
    runs: Sequence[SourceRun],
    *,
    check_statements: bool = False,
    now: datetime | None = None,
    http_transport: httpx2.AsyncBaseTransport | None = None,
) -> int:
    """Registra las corridas, revisa los estados vencidos (si se pide) y avisa lo nuevo.

    Devuelve cuántos incidentes se avisaron. Los avisos van solo a `ADMIN_EMAILS`.
    """
    now = now or datetime.now(UTC)
    engine = create_engine(settings)
    try:
        sessions = create_session_factory(engine)
        monitor = IntegrationMonitor(sessions, load_monitor_config(settings.config_dir))
        await monitor.record(runs, now)
        if check_statements:
            universe = load_universe(settings.config_dir / UNIVERSE_FILE)
            fiscal_ends = {c.clave: c.cnv.cierre_ejercicio for c in universe.empresas if c.cnv}
            async with sessions() as session:
                published = await published_statement_periods(session)
            today = now.astimezone(ZoneInfo(settings.app_timezone)).date()
            await monitor.check_statements(fiscal_ends, published, today, now)
        async with httpx2.AsyncClient(transport=http_transport, timeout=TIMEOUT_SECONDS) as http:
            sender = ResendEmailSender(settings, http)
            return await monitor.notify(sender, settings.admin_emails, now)
    finally:
        await engine.dispose()


async def report_runs_safely(
    settings: Settings, runs: Sequence[SourceRun], *, check_statements: bool = False
) -> None:
    """Como `report_runs`, pero una falla del monitor no corta la tarea que lo llama.

    La ingesta de datos es más importante que el aviso: si el monitor falla, queda en el log
    con nivel error y la tarea sigue.
    """
    try:
        await report_runs(settings, runs, check_statements=check_statements)
    except Exception:
        logger.exception("monitor_con_error", corridas=len(runs))
