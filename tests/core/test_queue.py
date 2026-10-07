"""Tareas periódicas: el horario de `.env` se interpreta en APP_TIMEZONE."""

from datetime import UTC, date, datetime

import brujula.features.prices.tasks  # noqa: F401  (registra la tarea de precios)
from brujula.core.config import Settings
from brujula.core.queue import create_queue_app
from brujula.features.prices.tasks import window_ending

# Miércoles 07/10/2026, 03:20 UTC (00:20 en Buenos Aires).
NOW = datetime(2026, 10, 7, 3, 20, tzinfo=UTC).timestamp()


def test_la_tarea_de_precios_queda_agendada_con_el_cron_de_env(settings: Settings) -> None:
    app = create_queue_app(settings)

    periodic = app.periodic_registry.periodic_tasks[("precios:actualizar_diario", "diario")]

    assert periodic.cron == settings.cron_prices_daily == "0 19 * * 1-5"


def test_el_cron_se_evalua_en_la_zona_horaria_de_la_app(settings: Settings) -> None:
    app = create_queue_app(settings)
    periodic = app.periodic_registry.periodic_tasks[("precios:actualizar_diario", "diario")]

    next_run = periodic.croniter.get_next(ret_type=float, start_time=NOW)

    # 19:00 en Buenos Aires (UTC-3) son las 22:00 UTC, no las 19:00 UTC.
    assert datetime.fromtimestamp(next_run, UTC) == datetime(2026, 10, 7, 22, 0, tzinfo=UTC)


def test_la_ventana_termina_en_la_fecha_local_del_horario_programado() -> None:
    # 22:00 UTC del 07/10 son las 19:00 del 07/10 en Buenos Aires.
    scheduled = int(datetime(2026, 10, 7, 22, 0, tzinfo=UTC).timestamp())

    assert window_ending(scheduled, "America/Argentina/Buenos_Aires") == (
        date(2026, 10, 1),
        date(2026, 10, 7),
    )
    # A las 01:00 UTC del 08/10 todavía es 07/10 en Buenos Aires.
    late = int(datetime(2026, 10, 8, 1, 0, tzinfo=UTC).timestamp())
    assert window_ending(late, "America/Argentina/Buenos_Aires")[1] == date(2026, 10, 7)
