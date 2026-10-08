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
from fastapi.testclient import TestClient
from pydantic import SecretStr
from testcontainers.core.container import DockerContainer
from testcontainers.core.docker_client import DockerClient

from brujula.core.config import Settings
from brujula.main import create_app
from tests.support.app import backend_options
from tests.support.google import FakeGoogle

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
def database(request: pytest.FixtureRequest) -> Iterator[Database]:
    if not _docker_available():
        message = "Docker no está disponible para los tests de base de datos"
        if request.config.getoption("--requiere-db"):
            pytest.fail(message)
        pytest.skip(message)
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


# --- App con base real y Google simulado (tests de integración de features) -------------


@pytest.fixture
def auth_settings(settings: Settings, database: Database) -> Settings:
    return settings.model_copy(
        update={
            "database_url": SecretStr(database.url("app")),
            "admin_emails": ["jefa@ejemplo.com"],
            "founder_plan_open": True,
        }
    )


@pytest.fixture
def google(auth_settings: Settings) -> FakeGoogle:
    return FakeGoogle(client_id=auth_settings.google_client_id)


@pytest.fixture
def superuser(database: Database) -> Iterator[psycopg.Connection]:
    """Conexión sin RLS para preparar datos y verificar la base. Limpia al terminar."""
    with psycopg.connect(database.conninfo("superuser"), autocommit=True) as conn:
        yield conn
        conn.execute("DELETE FROM users")  # en cascada: sesiones y posiciones
        conn.execute("DELETE FROM oauth_transactions")
        conn.execute("DELETE FROM verification_issues")
        conn.execute("DELETE FROM financial_facts")
        conn.execute("DELETE FROM document_checks")
        conn.execute("DELETE FROM cnv_statements")
        conn.execute("DELETE FROM documents")
        conn.execute("DELETE FROM prices_daily")
        conn.execute("DELETE FROM fx_daily")
        conn.execute("DELETE FROM instruments")
        conn.execute("DELETE FROM companies")


def make_client(settings: Settings, google: FakeGoogle) -> TestClient:
    app = create_app(settings, http_transport=google.transport())
    return TestClient(app, base_url="https://testserver", backend_options=backend_options())


@pytest.fixture
def client(
    auth_settings: Settings, google: FakeGoogle, superuser: psycopg.Connection
) -> Iterator[TestClient]:
    with make_client(auth_settings, google) as test_client:
        yield test_client
