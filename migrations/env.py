"""Entorno de Alembic. Corre siempre con el rol de migraciones (dueño del esquema).

Usa el driver psycopg en modo sincrónico: las migraciones no necesitan async y así
funcionan igual en Linux y en Windows.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# Registran sus tablas en Base.metadata (para autogenerate).
import brujula.core.llm.records
import brujula.features.auth.models
import brujula.features.documents.models
import brujula.features.portfolios.models
import brujula.features.prices.models
import brujula.features.universe.models  # noqa: F401
from brujula.core.config import get_settings
from brujula.core.db import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)


def _database_url() -> str:
    # Los tests pasan la URL explícitamente; en uso normal sale de la configuración.
    url = config.attributes.get("database_url")
    if isinstance(url, str):
        return url
    return get_settings().database_url_migrations.get_secret_value()


def run_migrations() -> None:
    if context.is_offline_mode():
        raise RuntimeError("Solo se admiten migraciones online (contra una base real).")
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
