"""Configuración: carga, variables faltantes y mensajes sin secretos (T0.2)."""

import base64
import re
from pathlib import Path

import pytest

from brujula.core.config import ConfigError, Settings, load_settings
from tests.support.env import VALID_ENV

ROOT = Path(__file__).resolve().parents[2]

# Variables de .env.example que usa docker/compose.yml y no la app.
COMPOSE_ONLY = {
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_APP_USER",
    "POSTGRES_APP_PASSWORD",
    "POSTGRES_MIGRATOR_USER",
    "POSTGRES_MIGRATOR_PASSWORD",
    "CADDY_SITE_ADDRESS",
}

OPTIONAL = {
    "LLM_EXTRACTION_FALLBACK_MODEL",
    "VALIDATION_THRESHOLD_PCT",
    "MERCADOPAGO_ACCESS_TOKEN",
    "MERCADOPAGO_WEBHOOK_SECRET",
}


def _error_for(env: pytest.MonkeyPatch, name: str, value: str) -> str:
    env.setenv(name, value)
    with pytest.raises(ConfigError) as exc_info:
        load_settings(env_file=None)
    return str(exc_info.value)


def test_carga_configuracion_valida(env: pytest.MonkeyPatch) -> None:
    settings = load_settings(env_file=None)

    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.admin_emails == ["admin@ejemplo.com", "otra@ejemplo.com"]
    assert settings.founder_plan_open is True
    assert settings.llm_embedding_dimensions == 1536
    assert settings.validation_threshold_pct is None
    assert settings.mercadopago_access_token is None


def test_los_secretos_no_aparecen_en_repr(env: pytest.MonkeyPatch) -> None:
    rendered = repr(load_settings(env_file=None))

    for name in ("SESSION_SECRET", "CSRF_SECRET", "OPENAI_API_KEY", "GOOGLE_CLIENT_SECRET"):
        assert VALID_ENV[name] not in rendered
    assert "clave-app" not in rendered  # la clave dentro de DATABASE_URL


@pytest.mark.parametrize("name", sorted(VALID_ENV))
def test_falta_variable_obligatoria(env: pytest.MonkeyPatch, name: str) -> None:
    env.delenv(name)

    with pytest.raises(ConfigError, match="Faltan variables obligatorias") as exc_info:
        load_settings(env_file=None)
    assert re.search(rf"\b{name}\b", str(exc_info.value))


def test_variable_vacia_cuenta_como_faltante(env: pytest.MonkeyPatch) -> None:
    message = _error_for(env, "SESSION_SECRET", "")
    assert "Faltan variables obligatorias: SESSION_SECRET" in message


def test_lista_todas_las_faltantes_juntas(env: pytest.MonkeyPatch) -> None:
    env.delenv("DATABASE_URL")
    env.delenv("RESEND_API_KEY")

    with pytest.raises(ConfigError, match="DATABASE_URL, RESEND_API_KEY"):
        load_settings(env_file=None)


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("SESSION_SECRET", "secreto-corto-filtrable", "al menos 32 caracteres"),
        ("FIELD_ENCRYPTION_KEY", "clave-que-no-es-base64!", "base64"),
        ("FIELD_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"corta").decode(), "32 bytes"),
        ("DATABASE_URL", "mysql://u:clave-filtrable@h/db", "postgresql+psycopg"),
        ("DATABASE_URL", "postgresql+asyncpg://u:clave-filtrable@h/db", "psycopg"),
        ("APP_TIMEZONE", "Marte/Olympus_Mons", "zona horaria"),
        ("CRON_WEEKLY_DIGEST", "0 8 * *", "cron de 5 campos"),
        ("LLM_EMBEDDING_DIMENSIONS", "3072", "2000"),
        ("ADMIN_EMAILS", "no-es-un-email", "email"),
        ("LOG_LEVEL", "verbose", "LOG_LEVEL"),
        ("PRICE_DIVERGENCE_THRESHOLD_PCT", "-1", "PRICE_DIVERGENCE_THRESHOLD_PCT"),
    ],
)
def test_valor_invalido_sin_filtrar_el_valor(
    env: pytest.MonkeyPatch, name: str, value: str, expected: str
) -> None:
    message = _error_for(env, name, value)

    assert "Variables con valor inválido" in message
    assert name in message
    assert expected in message
    assert value not in message


def test_produccion_exige_https(env: pytest.MonkeyPatch) -> None:
    message = _error_for(env, "APP_ENV", "production")
    assert "https" in message
    assert "APP_BASE_URL" in message


def test_lee_el_archivo_env(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "".join(f'{name}="{value}"\n' for name, value in VALID_ENV.items()), encoding="utf-8"
    )
    for name in VALID_ENV:
        env.delenv(name)

    settings = load_settings(env_file=env_file)

    assert settings.cron_weekly_digest == "0 8 * * 6"


def test_env_example_documenta_exactamente_las_variables() -> None:
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    documented = {m.group(1) for line in lines if (m := re.match(r"^([A-Z0-9_]+)=", line))}
    declared = {name.upper() for name in Settings.model_fields}

    assert documented == declared | COMPOSE_ONLY
    assert declared == set(VALID_ENV) | OPTIONAL
