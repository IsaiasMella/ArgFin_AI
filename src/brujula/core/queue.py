"""Cola de tareas sobre PostgreSQL (Procrastinate).

Cada feature define sus tareas en `tasks.py` y registra en `BLUEPRINTS` una función que
arma su `procrastinate.Blueprint` (y en `PERIODIC_JOBS` si corre por cron); así ninguna
tarea necesita la configuración al importarse. Es una fábrica porque Procrastinate modifica
el blueprint al agregarlo a una app: cada app necesita uno nuevo.

El esquema de la cola lo crean las migraciones de Alembic (no `procrastinate schema`).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import croniter
from procrastinate import App, Blueprint, PsycopgConnector

from brujula.core.config import Settings
from brujula.core.db import libpq_conninfo

BLUEPRINTS: dict[str, Callable[[], Blueprint]] = {}


@dataclass(frozen=True)
class PeriodicJob:
    task_name: str  # con namespace, p. ej. "precios:actualizar_diario"
    periodic_id: str
    cron: Callable[[Settings], str]  # el horario sale de `.env`


PERIODIC_JOBS: list[PeriodicJob] = []


def _schedule(app: App, job: PeriodicJob, settings: Settings) -> None:
    cron = job.cron(settings)
    task = app.tasks[job.task_name]
    app.periodic(cron=cron, periodic_id=job.periodic_id)(task)
    # Procrastinate evalúa el cron en UTC. croniter lo evalúa en la zona de la fecha con que
    # se crea, así que el horario de `.env` se interpreta en APP_TIMEZONE.
    periodic = app.periodic_registry.periodic_tasks[(task.name, job.periodic_id)]
    periodic.__dict__["croniter"] = croniter.croniter(
        cron, datetime.now(ZoneInfo(settings.app_timezone))
    )


def create_queue_app(settings: Settings) -> App:
    connector = PsycopgConnector(conninfo=libpq_conninfo(settings.database_url.get_secret_value()))
    app = App(connector=connector)
    for namespace, build_blueprint in BLUEPRINTS.items():
        app.add_tasks_from(build_blueprint(), namespace=namespace)
    for job in PERIODIC_JOBS:
        _schedule(app, job, settings)
    return app
