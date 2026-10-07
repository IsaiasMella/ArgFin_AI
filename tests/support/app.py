"""Helpers para levantar la app con base real y Google simulado en tests de integración."""

import asyncio
import sys
from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from brujula.core.security.csrf import CSRF_HEADER
from brujula.features.auth.cookies import CSRF_COOKIE
from tests.support.google import FakeGoogle

# Origen permitido para CSRF: WEB_BASE_URL de tests/support/env.py.
WEB_ORIGIN = "http://localhost:3000"


def backend_options() -> dict[str, Any]:
    # psycopg asíncrono necesita SelectorEventLoop en Windows.
    return {"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {}


def login(client: TestClient, google: FakeGoogle, **claims: Any) -> dict[str, str]:
    """Hace el flujo completo de ingreso. Devuelve los parámetros de la URL de Google."""
    start = client.get("/auth/google/login", follow_redirects=False)
    assert start.status_code == 302, start.text
    params = {k: v[0] for k, v in parse_qs(urlsplit(start.headers["location"]).query).items()}
    google.expected_challenge = params["code_challenge"]
    google.next_claims = google.claims(nonce=params["nonce"], **claims)
    done = client.get(
        "/auth/google/callback",
        params={"code": "codigo-de-google", "state": params["state"]},
        follow_redirects=False,
    )
    assert done.status_code == 302, done.text
    return params


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {"Origin": WEB_ORIGIN, CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""}
