"""Configuración de la aplicación.

Único punto del código que lee variables de entorno (y el archivo `.env`, vía
pydantic-settings). El resto de los módulos recibe la configuración con `get_settings()`.

No hay valores por defecto: toda variable obligatoria que falte (o esté vacía) impide
arrancar, con un mensaje que lista los nombres afectados sin mostrar sus valores.
"""

import base64
import binascii
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    HttpUrl,
    PostgresDsn,
    SecretStr,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENV_FILE = Path(".env")
MIN_SECRET_LENGTH = 32
AES_256_KEY_BYTES = 32
# Límite de dimensiones de los índices HNSW/IVFFlat de pgvector para el tipo `vector`.
PGVECTOR_MAX_INDEXED_DIMENSIONS = 2000
CRON_FIELDS = 5


class ConfigError(RuntimeError):
    """La configuración es incompleta o inválida."""


# --- Validadores ------------------------------------------------------------
# Los mensajes nunca incluyen el valor recibido: puede ser un secreto.

_POSTGRES_DSN: TypeAdapter[PostgresDsn] = TypeAdapter(PostgresDsn)
# Un solo driver para SQLAlchemy, Alembic y la cola de tareas (ver docs/adr/003).
DATABASE_SCHEME = "postgresql+psycopg"


def _validate_dsn(value: SecretStr) -> SecretStr:
    message = f"debe ser una URL de PostgreSQL con el driver psycopg ({DATABASE_SCHEME}://...)"
    try:
        dsn = _POSTGRES_DSN.validate_python(value.get_secret_value())
    except ValidationError:
        raise ValueError(message) from None
    if dsn.scheme != DATABASE_SCHEME:
        raise ValueError(message)
    return value


def _validate_secret_length(value: SecretStr) -> SecretStr:
    if len(value.get_secret_value()) < MIN_SECRET_LENGTH:
        raise ValueError(f"debe tener al menos {MIN_SECRET_LENGTH} caracteres")
    return value


def _validate_aes_key(value: SecretStr) -> SecretStr:
    try:
        raw = base64.urlsafe_b64decode(value.get_secret_value().encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise ValueError("debe estar codificada en base64 URL-safe") from None
    if len(raw) != AES_256_KEY_BYTES:
        raise ValueError(f"debe decodificar a {AES_256_KEY_BYTES} bytes (AES-256)")
    return value


def _validate_cron(value: str) -> str:
    if len(value.split()) != CRON_FIELDS:
        raise ValueError(f"debe ser una expresión cron de {CRON_FIELDS} campos")
    return value


def _validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(
            "debe ser una zona horaria IANA (p. ej. America/Argentina/Buenos_Aires)"
        ) from None
    return value


def _validate_email_like(value: str) -> str:
    if "@" not in value:
        raise ValueError("debe contener una dirección de email")
    return value


def _split_emails(value: object) -> object:
    if isinstance(value, str):
        return [email.strip().lower() for email in value.split(",") if email.strip()]
    return value


def _upper(value: object) -> object:
    return value.upper() if isinstance(value, str) else value


DatabaseDsn = Annotated[SecretStr, AfterValidator(_validate_dsn)]
StrongSecret = Annotated[SecretStr, AfterValidator(_validate_secret_length)]
AesKey = Annotated[SecretStr, AfterValidator(_validate_aes_key)]
CronExpression = Annotated[str, AfterValidator(_validate_cron)]
Timezone = Annotated[str, AfterValidator(_validate_timezone)]
EmailLike = Annotated[str, AfterValidator(_validate_email_like)]
NonEmptyStr = Annotated[str, Field(min_length=1)]
Percentage = Annotated[Decimal, Field(gt=0, le=100)]
PositiveDecimal = Annotated[Decimal, Field(gt=0)]


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


# Claves anteriores: solo para descifrar durante una rotación (ver docs/adr/008).
AesKeyList = Annotated[list[AesKey], NoDecode, BeforeValidator(_split_csv)]
EmailList = Annotated[
    list[Annotated[str, AfterValidator(_validate_email_like)]],
    NoDecode,
    BeforeValidator(_split_emails),
    Field(min_length=1),
]


class Settings(BaseSettings):
    """Variables de entorno de `02-plan-tecnico.md`, sección 4."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        # Una variable vacía (como quedan al copiar .env.example) cuenta como faltante.
        env_ignore_empty=True,
        # El .env puede tener variables de otros servicios (p. ej. docker compose).
        extra="ignore",
        frozen=True,
    )

    # App
    app_env: Literal["development", "test", "staging", "production"]
    app_base_url: HttpUrl
    web_base_url: HttpUrl
    cookie_domain: NonEmptyStr
    log_level: Annotated[
        Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], BeforeValidator(_upper)
    ]
    app_timezone: Timezone

    # Base de datos
    database_url: DatabaseDsn
    database_url_migrations: DatabaseDsn

    # Seguridad
    session_secret: StrongSecret
    field_encryption_key: AesKey
    field_encryption_keys_previous: AesKeyList | None = None
    csrf_secret: StrongSecret
    admin_emails: EmailList

    # Sesiones y límites de pedidos
    session_ttl_hours: Annotated[int, Field(gt=0, le=24 * 30)]
    rate_limit_auth_per_minute: Annotated[int, Field(gt=0)]
    rate_limit_upload_per_hour: Annotated[int, Field(gt=0)]

    # Carga de portafolio por CSV
    csv_max_bytes: Annotated[int, Field(gt=0, le=10 * 1024 * 1024)]
    csv_max_rows: Annotated[int, Field(gt=0)]

    # OAuth (Google)
    google_client_id: NonEmptyStr
    google_client_secret: SecretStr
    google_redirect_uri: HttpUrl
    google_discovery_url: HttpUrl

    # LLMs
    llm_extraction_model: NonEmptyStr
    llm_extraction_fallback_model: NonEmptyStr | None = None
    llm_classification_model: NonEmptyStr
    llm_writer_model: NonEmptyStr
    llm_embedding_model: NonEmptyStr
    llm_embedding_dimensions: Annotated[int, Field(gt=0, le=PGVECTOR_MAX_INDEXED_DIMENSIONS)]
    llm_judge_model: NonEmptyStr
    anthropic_api_key: SecretStr
    openai_api_key: SecretStr
    llm_monthly_budget_usd: PositiveDecimal

    # Observabilidad
    langfuse_public_key: NonEmptyStr
    langfuse_secret_key: SecretStr
    langfuse_host: HttpUrl

    # Fuentes de datos
    sec_user_agent: EmailLike  # la SEC exige un User-Agent con email de contacto
    byma_open_data_base_url: HttpUrl
    data912_base_url: HttpUrl
    price_divergence_threshold_pct: Percentage

    # Programación de tareas
    cron_prices_daily: CronExpression
    cron_filings_check: CronExpression
    cron_news_ingest: CronExpression
    cron_weekly_digest: CronExpression

    # Email
    resend_api_key: SecretStr
    email_from: EmailLike
    resend_webhook_secret: SecretStr

    # Negocio
    price_pro_ars: PositiveDecimal
    free_plan_max_positions: Annotated[int, Field(gt=0)]
    free_plan_digests_per_month: Annotated[int, Field(ge=0)]
    founder_plan_open: bool
    validation_threshold_pct: Percentage | None = None  # se define en T9.1

    # Pagos (fase 9)
    mercadopago_access_token: SecretStr | None = None
    mercadopago_webhook_secret: SecretStr | None = None

    # Almacenamiento y rutas
    document_storage_dir: Path
    config_dir: Path
    prompts_dir: Path

    @model_validator(mode="after")
    def _https_outside_development(self) -> "Settings":
        if self.app_env in {"development", "test"}:
            return self
        insecure = [
            name.upper()
            for name in ("app_base_url", "web_base_url", "google_redirect_uri")
            if getattr(self, name).scheme != "https"
        ]
        if insecure:
            raise ValueError(f"deben usar https fuera de desarrollo: {', '.join(insecure)}")
        return self


def _format_errors(exc: ValidationError) -> str:
    missing: list[str] = []
    invalid: list[str] = []
    for error in exc.errors(include_input=False, include_url=False):
        name = str(error["loc"][0]).upper() if error["loc"] else "configuración"
        if error["type"] == "missing":
            missing.append(name)
        else:
            invalid.append(f"  - {name}: {error['msg']}")
    lines = ["Configuración inválida; revisá tu .env (ver .env.example)."]
    if missing:
        lines.append("Faltan variables obligatorias: " + ", ".join(sorted(set(missing))))
    if invalid:
        lines.append("Variables con valor inválido:")
        lines.extend(invalid)
    return "\n".join(lines)


def load_settings(env_file: Path | None = ENV_FILE) -> Settings:
    """Construye la configuración; `env_file=None` lee solo el entorno del proceso."""
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        # `from None`: el error original incluye los valores recibidos (posibles secretos).
        raise ConfigError(_format_errors(exc)) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Configuración de la aplicación, cargada una sola vez por proceso."""
    return load_settings()
