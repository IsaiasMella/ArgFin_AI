"""Casos de uso de autenticación: iniciar y completar el ingreso, resolver y cerrar sesión.

Es la única puerta de la feature `auth` para el resto del sistema.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from brujula.core.config import Settings
from brujula.core.db import user_transaction
from brujula.core.security.hashing import derive_key, pseudonymize, token_hash
from brujula.features.auth import repository
from brujula.features.auth.models import User
from brujula.features.auth.oidc import GoogleOIDC, OIDCError, new_pkce_pair
from brujula.features.auth.repository import SessionOwner

logger = structlog.get_logger(__name__)

OAUTH_TRANSACTION_TTL = timedelta(minutes=10)
USER_AGENT_MAX_LENGTH = 512


class LoginRejectedError(Exception):
    """El ingreso no se completa. `reason` es para los logs, nunca para el usuario."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class LoginStart:
    state: str
    authorization_url: str


@dataclass(frozen=True)
class NewSession:
    token: str
    user_id: UUID
    expires: datetime


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    rol: str
    plan: str
    session_token: str


class AuthService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        oidc: GoogleOIDC,
    ) -> None:
        self._sessions = session_factory
        self._oidc = oidc
        self._session_ttl = timedelta(hours=settings.session_ttl_hours)
        self._admin_emails = set(settings.admin_emails)
        self._plan_for_new_users = "fundador" if settings.founder_plan_open else "gratis"
        self._pseudonym_key = derive_key(settings.session_secret.get_secret_value(), "logs")

    def pseudonym(self, value: str) -> str:
        return pseudonymize(value, self._pseudonym_key)

    async def start_login(self) -> LoginStart:
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        pkce = new_pkce_pair()
        url = await self._oidc.authorization_url(
            state=state, nonce=nonce, code_challenge=pkce.challenge
        )
        async with self._sessions() as session, session.begin():
            await repository.save_transaction(
                session,
                state_hash=token_hash(state),
                code_verifier=pkce.verifier,
                nonce=nonce,
                expires=datetime.now(UTC) + OAUTH_TRANSACTION_TTL,
            )
        return LoginStart(state=state, authorization_url=url)

    async def complete_login(
        self,
        *,
        code: str | None,
        state: str | None,
        state_cookie: str | None,
        provider_error: str | None,
        ip: str | None,
        user_agent: str | None,
    ) -> NewSession:
        if provider_error:
            raise LoginRejectedError(f"proveedor:{provider_error}")
        if not state or not code:
            raise LoginRejectedError("faltan_code_o_state")
        # El state tiene que coincidir con la cookie del mismo navegador (CSRF de ingreso).
        if not state_cookie or not secrets.compare_digest(state, state_cookie):
            raise LoginRejectedError("state_no_coincide")

        async with self._sessions() as session, session.begin():
            pending = await repository.take_transaction(session, token_hash(state))
        if pending is None:
            raise LoginRejectedError("state_desconocido_o_vencido")

        try:
            id_token = await self._oidc.exchange_code(
                code=code, code_verifier=pending.code_verifier
            )
            identity = await self._oidc.verify_id_token(id_token, nonce=pending.nonce)
        except OIDCError as exc:
            raise LoginRejectedError(exc.reason) from None

        async with self._sessions() as session, session.begin():
            user_id = await repository.upsert_oauth_user(
                session,
                provider=self._oidc.provider,
                subject=identity.subject,
                email=identity.email,
                name=identity.name,
                plan_for_new_user=self._plan_for_new_users,
                is_admin=identity.email in self._admin_emails,
            )

        token = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + self._session_ttl
        async with user_transaction(self._sessions, user_id) as session:
            await repository.create_session(
                session,
                user_id=user_id,
                token_hash=token_hash(token),
                expires=expires,
                ip_hash=self.pseudonym(ip) if ip else None,
                user_agent=user_agent[:USER_AGENT_MAX_LENGTH] if user_agent else None,
            )
        logger.info("login_ok", usuario=self.pseudonym(str(user_id)))
        return NewSession(token=token, user_id=user_id, expires=expires)

    async def authenticate(self, session_token: str) -> AuthenticatedUser | None:
        async with self._sessions() as session, session.begin():
            owner: SessionOwner | None = await repository.resolve_session(
                session, token_hash(session_token)
            )
        if owner is None:
            return None
        return AuthenticatedUser(
            id=owner.user_id, rol=owner.rol, plan=owner.plan, session_token=session_token
        )

    async def logout(self, user: AuthenticatedUser) -> None:
        async with user_transaction(self._sessions, user.id) as session:
            await repository.delete_session(session, token_hash(user.session_token))
        logger.info("logout", usuario=self.pseudonym(str(user.id)))

    async def get_profile(self, user: AuthenticatedUser) -> User | None:
        async with user_transaction(self._sessions, user.id) as session:
            return await repository.get_user(session, user.id)
