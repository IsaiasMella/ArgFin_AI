"""Tareas de precios en la cola: la corrida diaria (cron de `.env`) y la carga manual."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx2
from procrastinate import Blueprint, RetryStrategy

from brujula.core.config import Settings, get_settings
from brujula.core.db import create_engine, create_session_factory
from brujula.core.queue import BLUEPRINTS, PERIODIC_JOBS, PeriodicJob
from brujula.features.prices.daily import FX_FILE, DailyRunReport, load_fx_config, run_daily_prices
from brujula.features.prices.providers import BymaOpenData, Data912

NAMESPACE = "precios"
# Cada corrida revisa la última semana: recupera días perdidos y completa datos tardíos.
LOOKBACK_DAYS = 7
# data912 entrega la historia completa de cada especie: más margen que para Google.
TIMEOUT_SECONDS = 30.0


async def update_prices(
    settings: Settings,
    *,
    start: date,
    end: date,
    http_transport: httpx2.AsyncBaseTransport | None = None,
) -> DailyRunReport:
    """Arma las dependencias desde la configuración y corre la tarea diaria."""
    fx = load_fx_config(settings.config_dir / FX_FILE)
    engine = create_engine(settings)
    try:
        async with httpx2.AsyncClient(transport=http_transport, timeout=TIMEOUT_SECONDS) as http:
            return await run_daily_prices(
                create_session_factory(engine),
                BymaOpenData(settings, http),
                Data912(settings, http),
                fx=fx,
                threshold_pct=settings.price_divergence_threshold_pct,
                start=start,
                end=end,
            )
    finally:
        await engine.dispose()


def window_ending(timestamp: int, timezone: str) -> tuple[date, date]:
    end = datetime.fromtimestamp(timestamp, ZoneInfo(timezone)).date()
    return end - timedelta(days=LOOKBACK_DAYS - 1), end


async def update_daily(timestamp: int) -> None:
    """La agenda Procrastinate con `timestamp`: el momento programado (epoch)."""
    settings = get_settings()
    start, end = window_ending(timestamp, settings.app_timezone)
    await update_prices(settings, start=start, end=end)


def build_blueprint() -> Blueprint:
    blueprint = Blueprint()
    # Idempotente: si falla (p. ej. ninguna fuente responde), se reintenta a los 15 minutos.
    blueprint.task(name="actualizar_diario", retry=RetryStrategy(max_attempts=3, wait=900))(
        update_daily
    )
    return blueprint


BLUEPRINTS[NAMESPACE] = build_blueprint
PERIODIC_JOBS.append(
    PeriodicJob(
        task_name=f"{NAMESPACE}:actualizar_diario",
        periodic_id="diario",
        cron=lambda settings: settings.cron_prices_daily,
    )
)
