"""Acceso a datos de autenticación. Cada función recibe la sesión de SQLAlchemy de la
transacción en curso; el contexto de RLS lo fija quien la abre (el servicio)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, insert, text
from sqlalchemy.ext.asyncio import AsyncSession

from brujula.features.auth.models import OAuthTransaction, User, UserSession


@dataclass(frozen=True)
class PendingTransaction:
    code_verifier: str
    nonce: str


@dataclass(frozen=True)
class SessionOwner:
    user_id: UUID
    rol: str
    plan: str


async def save_transaction(
    session: AsyncSession, *, state_hash: bytes, code_verifier: str, nonce: str, expires: datetime
) -> None:
    # Limpieza oportunista: cada ingreso borra los flujos vencidos que quedaron sin usar.
    await session.execute(delete(OAuthTransaction).where(OAuthTransaction.expira_en < func.now()))
    await session.execute(
        insert(OAuthTransaction).values(
            state_hash=state_hash, code_verifier=code_verifier, nonce=nonce, expira_en=expires
        )
    )


async def take_transaction(session: AsyncSession, state_hash: bytes) -> PendingTransaction | None:
    """Consume la transacción (un solo uso) si existe y no venció."""
    row = (
        await session.execute(
            delete(OAuthTransaction)
            .where(OAuthTransaction.state_hash == state_hash)
            .returning(
                OAuthTransaction.code_verifier, OAuthTransaction.nonce, OAuthTransaction.expira_en
            )
        )
    ).one_or_none()
    if row is None or row.expira_en <= datetime.now(UTC):
        return None
    return PendingTransaction(row.code_verifier, row.nonce)


async def upsert_oauth_user(
    session: AsyncSession,
    *,
    provider: str,
    subject: str,
    email: str,
    name: str | None,
    plan_for_new_user: str,
    is_admin: bool,
) -> UUID:
    user_id = await session.scalar(
        text("SELECT upsert_usuario_oauth(:provider, :subject, :email, :name, :plan, :is_admin)"),
        {
            "provider": provider,
            "subject": subject,
            "email": email,
            "name": name,
            "plan": plan_for_new_user,
            "is_admin": is_admin,
        },
    )
    if not isinstance(user_id, UUID):
        raise RuntimeError("upsert_usuario_oauth no devolvió un id")
    return user_id


async def create_session(
    session: AsyncSession,
    *,
    user_id: UUID,
    token_hash: bytes,
    expires: datetime,
    ip_hash: str | None,
    user_agent: str | None,
) -> None:
    """Requiere el contexto RLS de `user_id` (la política solo deja insertar lo propio)."""
    await session.execute(
        insert(UserSession).values(
            user_id=user_id,
            token_hash=token_hash,
            expira_en=expires,
            ip_hash=ip_hash,
            user_agent=user_agent,
        )
    )


async def resolve_session(session: AsyncSession, token_hash: bytes) -> SessionOwner | None:
    row = (
        await session.execute(
            text("SELECT user_id, rol, plan FROM resolver_sesion(:token_hash)"),
            {"token_hash": token_hash},
        )
    ).one_or_none()
    return SessionOwner(row.user_id, row.rol, row.plan) if row else None


async def delete_session(session: AsyncSession, token_hash: bytes) -> None:
    """Requiere el contexto RLS del dueño de la sesión."""
    await session.execute(delete(UserSession).where(UserSession.token_hash == token_hash))


async def get_user(session: AsyncSession, user_id: UUID) -> User | None:
    """Requiere el contexto RLS de `user_id`."""
    return await session.get(User, user_id)
