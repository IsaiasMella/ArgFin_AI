"""App FastAPI: arranque y `/health` (T0.3)."""

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from brujula.core.config import ConfigError, Settings, get_settings
from brujula.main import create_app


def test_health_responde_ok(settings: Settings) -> None:
    client = TestClient(create_app(settings))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_docs_ocultas_en_produccion(settings: Settings) -> None:
    production = settings.model_copy(update={"app_env": "production"})
    client = TestClient(create_app(production))

    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_la_app_no_arranca_sin_configuracion(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.delenv("DATABASE_URL")
    env.chdir(tmp_path)  # directorio sin .env
    get_settings.cache_clear()
    try:
        with pytest.raises(ConfigError, match="Faltan variables obligatorias: DATABASE_URL"):
            create_app()
    finally:
        get_settings.cache_clear()


def test_ready_sin_base_responde_503(settings: Settings) -> None:
    # Puerto 1 en localhost: conexión rechazada al instante, sin red externa.
    unreachable = settings.model_copy(
        update={"database_url": SecretStr("postgresql+psycopg://u:p@127.0.0.1:1/db")}
    )
    backend_options: dict[str, Any] = {}
    if sys.platform == "win32":
        backend_options["loop_factory"] = asyncio.SelectorEventLoop

    with TestClient(create_app(unreachable), backend_options=backend_options) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": False, "queue": False}


def test_genera_request_id_si_no_viene(settings: Settings) -> None:
    response = TestClient(create_app(settings)).get("/health")

    assert len(response.headers["X-Request-ID"]) == 32


def test_respeta_un_request_id_valido_y_reemplaza_uno_peligroso(settings: Settings) -> None:
    client = TestClient(create_app(settings))

    kept = client.get("/health", headers={"X-Request-ID": "caddy-1234-abcd"})
    replaced = client.get("/health", headers={"X-Request-ID": "x\ninyectado=1"})

    assert kept.headers["X-Request-ID"] == "caddy-1234-abcd"
    assert replaced.headers["X-Request-ID"] != "x\ninyectado=1"
