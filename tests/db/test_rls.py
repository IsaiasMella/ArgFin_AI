"""Row Level Security con el rol de la app (criterio de T0.4)."""

from collections.abc import AsyncIterator, Iterator
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from brujula.core.db import create_session_factory, set_user_context, user_transaction
from brujula.features.auth.models import User
from tests.db.conftest import CREDENTIALS, Database

pytestmark = pytest.mark.anyio

USER_A = UUID("00000000-0000-4000-8000-00000000000a")
USER_B = UUID("00000000-0000-4000-8000-00000000000b")


@pytest.fixture
def users(database: Database) -> Iterator[None]:
    """Dos usuarios cargados como superusuario (que no está sujeto a RLS)."""
    with psycopg.connect(database.conninfo("superuser"), autocommit=True) as conn:
        for user_id, email in ((USER_A, "a@ejemplo.com"), (USER_B, "b@ejemplo.com")):
            conn.execute(
                "INSERT INTO users (id, email, proveedor_oauth, sub_oauth, plan)"
                " VALUES (%s, %s, 'google', %s, 'fundador')",
                (user_id, email, f"sub-{email}"),
            )
        yield
        conn.execute("DELETE FROM users")


@pytest.fixture
async def app_sessions(database: Database) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database.url("app"))
    yield create_session_factory(engine)
    await engine.dispose()


async def _visible_ids(session: AsyncSession) -> set[UUID]:
    return set((await session.scalars(select(User.id))).all())


@pytest.mark.usefixtures("users")
async def test_sin_contexto_de_usuario_no_devuelve_filas(
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with app_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.usefixtures("users")
async def test_con_contexto_ve_solo_su_fila(
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with user_transaction(app_sessions, USER_A) as session:
        assert await _visible_ids(session) == {USER_A}
    async with user_transaction(app_sessions, USER_B) as session:
        assert await _visible_ids(session) == {USER_B}


@pytest.mark.usefixtures("users")
async def test_el_contexto_no_sobrevive_a_la_transaccion(
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with app_sessions() as session:
        async with session.begin():
            await set_user_context(session, USER_A)
            assert await _visible_ids(session) == {USER_A}
        # Misma conexión, transacción nueva: la variable quedó en '' y no debe fallar.
        async with session.begin():
            assert await _visible_ids(session) == set()


@pytest.mark.usefixtures("users")
async def test_no_puede_modificar_filas_de_otro_usuario(
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with user_transaction(app_sessions, USER_A) as session:
        result = await session.execute(
            update(User).where(User.id == USER_B).values(nombre="intruso")
        )
        assert result.rowcount == 0  # type: ignore[attr-defined]


@pytest.mark.usefixtures("users")
async def test_no_puede_insertar_filas_de_otro_usuario(
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with user_transaction(app_sessions, USER_A) as session:
            session.add(
                User(
                    id=uuid4(),
                    email="otro@ejemplo.com",
                    proveedor_oauth="google",
                    sub_oauth="sub-otro",
                    plan="gratis",
                )
            )


async def test_el_rol_de_la_app_no_puede_saltear_rls(
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with app_sessions() as session:
        role = (
            await session.execute(
                text(
                    "SELECT rolsuper, rolbypassrls,"
                    " (SELECT tableowner FROM pg_tables WHERE tablename = 'users')"
                    " FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
        assert role == (False, False, CREDENTIALS["migrator"][0])

    with pytest.raises(DBAPIError, match="must be owner"):
        async with app_sessions() as session, session.begin():
            await session.execute(text("ALTER TABLE users DISABLE ROW LEVEL SECURITY"))


async def test_users_tiene_rls_forzado(database: Database) -> None:
    with psycopg.connect(database.conninfo("migrator")) as conn:
        row = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'users'"
        ).fetchone()
    assert row == (True, True)
