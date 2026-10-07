"""Endpoints de autenticación (HU-01) y perfil del usuario."""

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from brujula.core.config import Settings
from brujula.features.auth.cookies import (
    STATE_COOKIE,
    clear_session_cookies,
    clear_state_cookie,
    set_session_cookies,
    set_state_cookie,
)
from brujula.features.auth.dependencies import (
    CsrfProtectedUser,
    CurrentUser,
    client_ip,
    get_auth_service,
    get_settings_from_app,
    rate_limited,
)
from brujula.features.auth.oidc import OIDCError
from brujula.features.auth.service import AuthService, LoginRejectedError

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["autenticación"])

Service = Annotated[AuthService, Depends(get_auth_service)]
AppSettings = Annotated[Settings, Depends(get_settings_from_app)]
auth_rate_limit = Depends(rate_limited("auth_rate_limiter"))


class Profile(BaseModel):
    id: UUID
    email: str
    nombre: str | None
    rol: str
    plan: str


def _web_url(settings: Settings, path: str = "") -> str:
    return str(settings.web_base_url).rstrip("/") + path


@router.get("/auth/google/login", dependencies=[auth_rate_limit])
async def login(service: Service) -> RedirectResponse:
    """Inicia el flujo OAuth 2.0 con PKCE y `state` y redirige a Google."""
    try:
        start = await service.start_login()
    except OIDCError as exc:
        logger.error("login_proveedor_no_disponible", motivo=exc.reason)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google no está disponible"
        ) from None
    response = RedirectResponse(start.authorization_url, status_code=status.HTTP_302_FOUND)
    set_state_cookie(response, start.state)
    return response


@router.get("/auth/google/callback", dependencies=[auth_rate_limit])
async def callback(
    request: Request,
    service: Service,
    settings: AppSettings,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Vuelta de Google: valida todo, crea la sesión y redirige al frontend."""
    ip = client_ip(request)
    try:
        new_session = await service.complete_login(
            code=code,
            state=state,
            state_cookie=request.cookies.get(STATE_COOKIE),
            provider_error=error,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
    except LoginRejectedError as exc:
        logger.warning("login_rechazado", motivo=exc.reason, ip=service.pseudonym(ip))
        response = RedirectResponse(
            _web_url(settings, "/ingresar?error=login_fallido"), status_code=status.HTTP_302_FOUND
        )
        clear_state_cookie(response)
        return response

    response = RedirectResponse(_web_url(settings), status_code=status.HTTP_302_FOUND)
    clear_state_cookie(response)
    set_session_cookies(response, settings, new_session.token)
    return response


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: CsrfProtectedUser, service: Service, settings: AppSettings) -> Response:
    """Invalida la sesión en el servidor y borra las cookies."""
    await service.logout(user)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookies(response, settings)
    return response


@router.get("/me")
async def me(user: CurrentUser, service: Service) -> Profile:
    profile = await service.get_profile(user)
    if profile is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o vencida")
    return Profile(
        id=profile.id,
        email=profile.email,
        nombre=profile.nombre,
        rol=profile.rol,
        plan=profile.plan,
    )
