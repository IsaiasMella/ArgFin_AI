"""Acceso a PostgreSQL: engine, sesiones y contexto de usuario para RLS.

Las políticas de RLS comparan `user_id` con `app_current_user_id()`, que lee la variable
de sesión `app.current_user_id`. Esa variable se fija con `set_config(..., true)`
(equivalente a `SET LOCAL`): vale solo dentro de la transacción en curso, así que una
conexión devuelta al pool nunca conserva el usuario anterior.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import MetaData, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine
from sqlalchemy.orm import DeclarativeBase

from brujula.core.config import Settings

USER_CONTEXT_SETTING = "app.current_user_id"

# Nombres de constraints estables: Alembic los necesita para generar migraciones.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_engine(settings: Settings) -> AsyncEngine:
    """Engine del rol de la app (sujeto a RLS). No abre conexiones hasta usarse."""
    return _create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def set_user_context(session: AsyncSession, user_id: UUID) -> None:
    """Fija el usuario para RLS hasta el fin de la transacción actual (`SET LOCAL`)."""
    await session.execute(
        text("SELECT set_config(:setting, :user_id, true)"),
        {"setting": USER_CONTEXT_SETTING, "user_id": str(user_id)},
    )


@asynccontextmanager
async def user_transaction(
    session_factory: async_sessionmaker[AsyncSession], user_id: UUID
) -> AsyncIterator[AsyncSession]:
    """Transacción que solo ve y modifica filas de `user_id`. Confirma al salir sin error."""
    async with session_factory() as session, session.begin():
        await set_user_context(session, user_id)
        yield session


def libpq_conninfo(database_url: str) -> str:
    """URL de SQLAlchemy (`postgresql+psycopg://`) a formato libpq, para psycopg directo."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
