"""Cola de tareas sobre PostgreSQL (Procrastinate).

Cada feature define sus tareas en `tasks.py` con un `procrastinate.Blueprint` y se
registra en `BLUEPRINTS`; así ninguna tarea necesita la configuración al importarse.
El esquema de la cola lo crean las migraciones de Alembic (no `procrastinate schema`).
"""

from procrastinate import App, Blueprint, PsycopgConnector

from brujula.core.config import Settings
from brujula.core.db import libpq_conninfo

BLUEPRINTS: dict[str, Blueprint] = {}


def create_queue_app(settings: Settings) -> App:
    connector = PsycopgConnector(conninfo=libpq_conninfo(settings.database_url.get_secret_value()))
    app = App(connector=connector)
    for namespace, blueprint in BLUEPRINTS.items():
        app.add_tasks_from(blueprint, namespace=namespace)
    return app
