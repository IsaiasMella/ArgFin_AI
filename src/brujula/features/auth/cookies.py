"""Cookies de sesión: `HttpOnly`, `Secure`, `SameSite=Lax`, dominio compartido app./api.

`brujula_csrf` no es HttpOnly a propósito: el frontend la lee para mandar `X-CSRF-Token`.
Con `COOKIE_DOMAIN=localhost` no se fija `Domain` (los navegadores lo rechazan) y la cookie
queda en el host, que se comparte entre puertos: alcanza para desarrollo.
"""

from fastapi import Response

from brujula.core.config import Settings
from brujula.core.security.csrf import csrf_token

SESSION_COOKIE = "brujula_sesion"
CSRF_COOKIE = "brujula_csrf"
STATE_COOKIE = "brujula_oauth_state"
STATE_COOKIE_PATH = "/auth/google"
STATE_COOKIE_MAX_AGE = 600


def _domain(settings: Settings) -> str | None:
    return None if settings.cookie_domain == "localhost" else settings.cookie_domain


def set_state_cookie(response: Response, state: str) -> None:
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_COOKIE_MAX_AGE,
        path=STATE_COOKIE_PATH,
        httponly=True,
        secure=True,
        samesite="lax",
    )


def clear_state_cookie(response: Response) -> None:
    response.delete_cookie(
        STATE_COOKIE, path=STATE_COOKIE_PATH, httponly=True, secure=True, samesite="lax"
    )


def set_session_cookies(response: Response, settings: Settings, token: str) -> None:
    max_age = settings.session_ttl_hours * 3600
    csrf = csrf_token(settings.csrf_secret.get_secret_value(), token)
    for name, value, httponly in ((SESSION_COOKIE, token, True), (CSRF_COOKIE, csrf, False)):
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            path="/",
            domain=_domain(settings),
            secure=True,
            httponly=httponly,
            samesite="lax",
        )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    for name, httponly in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(
            name,
            path="/",
            domain=_domain(settings),
            secure=True,
            httponly=httponly,
            samesite="lax",
        )
