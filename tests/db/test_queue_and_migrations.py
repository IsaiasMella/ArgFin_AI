"""Cola de tareas, `/ready` y reversibilidad de las migraciones (T0.4)."""

import asyncio
import sys
from typing import Any

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect

from brujula.core.config import Settings
from brujula.core.queue import create_queue_app
from brujula.main import create_app
from tests.db.conftest import Database, alembic_config

QUEUE_TABLES = {"procrastinate_jobs", "procrastinate_events", "procrastinate_workers"}


def _settings_for(settings: Settings, database: Database) -> Settings:
    return settings.model_copy(update={"database_url": SecretStr(database.url("app"))})


@pytest.mark.anyio
async def test_la_app_encola_tareas_con_su_rol(settings: Settings, database: Database) -> None:
    queue = create_queue_app(_settings_for(settings, database))
    async with queue.open_async():
        job_id = await queue.configure_task("tarea_de_prueba", queue="pruebas").defer_async()
    try:
        with psycopg.connect(database.conninfo("app")) as conn:
            row = conn.execute(
                "SELECT task_name, status FROM procrastinate_jobs WHERE id = %s", (job_id,)
            ).fetchone()
        assert row == ("tarea_de_prueba", "todo")
    finally:
        with psycopg.connect(database.conninfo("app"), autocommit=True) as conn:
            conn.execute("DELETE FROM procrastinate_jobs WHERE id = %s", (job_id,))


def test_ready_con_base_y_cola_disponibles(settings: Settings, database: Database) -> None:
    backend_options: dict[str, Any] = {}
    if sys.platform == "win32":
        backend_options["loop_factory"] = asyncio.SelectorEventLoop
    app = create_app(_settings_for(settings, database))

    with TestClient(app, backend_options=backend_options) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": True, "queue": True}


def test_migraciones_reversibles(database: Database) -> None:
    config = alembic_config(database)
    engine = create_engine(database.url("migrator"))
    try:
        command.downgrade(config, "base")
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}

        command.upgrade(config, "head")
        tables = set(inspect(engine).get_table_names())
        assert {"users", "roles"} | QUEUE_TABLES <= tables
    finally:
        engine.dispose()
