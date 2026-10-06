"""App FastAPI: arranque y `/health` (T0.3)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
