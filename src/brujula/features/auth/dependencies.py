"""Dependencias de FastAPI que otras features usan para exigir un usuario autenticado."""

from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, status

from brujula.core.config import Settings
from brujula.core.security.csrf import CSRF_HEADER, csrf_valid, origin_allowed, origin_of
from brujula.core.security.hashing import token_hash
from brujula.core.security.rate_limit import RateLimiter
from brujula.features.auth.cookies import SESSION_COOKIE
from brujula.features.auth.service import AuthenticatedUser, AuthService

logger = structlog.get_logger(__name__)


def get_settings_from_app(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth_service
    return service


def client_ip(request: Request) -> str:
    # Uvicorn ya resolvió X-Forwarded-For (solo confía en Caddy, el único que llega a la API).
    return request.client.host if request.client else "desconocida"


async def current_user(
    request: Request, service: Annotated[AuthService, Depends(get_auth_service)]
) -> AuthenticatedUser:
    token = request.cookies.get(SESSION_COOKIE)
    user = await service.authenticate(token) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o vencida")
    structlog.contextvars.bind_contextvars(usuario=service.pseudonym(str(user.id)))
    return user


async def csrf_protected_user(
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(current_user)],
    settings: Annotated[Settings, Depends(get_settings_from_app)],
) -> AuthenticatedUser:
    """Para métodos que modifican estado: valida Origin y el token CSRF de doble envío."""
    allowed = {origin_of(str(settings.web_base_url)), origin_of(str(settings.app_base_url))}
    if not origin_allowed(request.headers.get("origin"), request.headers.get("referer"), allowed):
        logger.warning("csrf_origen_rechazado", origen=request.headers.get("origin"))
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Origen no permitido")
    secret = settings.csrf_secret.get_secret_value()
    if not csrf_valid(request.headers.get(CSRF_HEADER), secret, user.session_token):
        logger.warning("csrf_token_invalido")
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Token CSRF inválido")
    return user


def rate_limited(
    limiter_name: str, *, per_session: bool = False
) -> Callable[[Request], Awaitable[None]]:
    """Dependencia que limita pedidos por IP (o por sesión) con el limitador indicado."""

    async def dependency(request: Request) -> None:
        limiter: RateLimiter = getattr(request.app.state, limiter_name)
        key = f"ip:{client_ip(request)}"
        session_token = request.cookies.get(SESSION_COOKIE)
        if per_session and session_token:
            key = f"sesion:{token_hash(session_token).hex()}"  # nunca el token en memoria
        if not limiter.allow(key):
            logger.warning("rate_limit_excedido", limitador=limiter_name)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, detail="Demasiados pedidos, probá en un rato"
            )

    return dependency


CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]
CsrfProtectedUser = Annotated[AuthenticatedUser, Depends(csrf_protected_user)]
