"""PostgreSQL efímero para tests de integración.

Levanta la misma imagen y el mismo script de roles que `docker/compose.yml`, y aplica las
migraciones con el rol de migraciones. Si Docker no está disponible, los tests se omiten.
"""

import asyncio
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from testcontainers.core.container import DockerContainer
from testcontainers.core.docker_client import DockerClient

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "pgvector/pgvector:pg16"
DB = "brujula_test"
CREDENTIALS = {
    "superuser": ("postgres", "clave-super-de-prueba"),
    "migrator": ("brujula_migrator", "clave-migr-de-prueba"),
    "app": ("brujula_app", "clave-app-de-prueba"),
}


@dataclass(frozen=True)
class Database:
    host: str
    port: int

    def url(self, role: str, driver: str = "postgresql+psycopg") -> str:
        user, password = CREDENTIALS[role]
        return f"{driver}://{user}:{password}@{self.host}:{self.port}/{DB}"

    def conninfo(self, role: str) -> str:
        return self.url(role, driver="postgresql")


def alembic_config(database: Database) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = database.url("migrator")
    config.attributes["configure_logger"] = False
    return config


def _docker_available() -> bool:
    try:
        DockerClient().client.ping()
    except Exception:
        return False
    return True


def _wait_for_tcp(conninfo: str, timeout_s: float = 60) -> None:
    # El servidor temporal de inicialización no escucha por TCP: si conecta, terminó.
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with psycopg.connect(conninfo, connect_timeout=2):
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    if not _docker_available():
        pytest.skip("Docker no está disponible: se omiten los tests de base de datos")
    superuser, super_password = CREDENTIALS["superuser"]
    container = (
        DockerContainer(IMAGE)
        .with_exposed_ports(5432)
        .with_volume_mapping(
            str(ROOT / "docker" / "postgres-init"), "/docker-entrypoint-initdb.d", "ro"
        )
        .with_envs(
            POSTGRES_USER=superuser,
            POSTGRES_PASSWORD=super_password,
            POSTGRES_DB=DB,
            POSTGRES_APP_USER=CREDENTIALS["app"][0],
            POSTGRES_APP_PASSWORD=CREDENTIALS["app"][1],
            POSTGRES_MIGRATOR_USER=CREDENTIALS["migrator"][0],
            POSTGRES_MIGRATOR_PASSWORD=CREDENTIALS["migrator"][1],
        )
    )
    with container:
        database = Database(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(5432)),
        )
        _wait_for_tcp(database.conninfo("migrator"))
        command.upgrade(alembic_config(database), "head")
        yield database


@pytest.fixture
def anyio_backend() -> tuple[str, dict[str, Any]]:
    # psycopg asíncrono necesita SelectorEventLoop en Windows.
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        options["loop_factory"] = asyncio.SelectorEventLoop
    return "asyncio", options
