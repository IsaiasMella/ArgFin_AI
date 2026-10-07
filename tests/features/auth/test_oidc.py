"""Validación del ID token de Google (T1.1: "token inválido")."""

import time
from collections.abc import AsyncIterator

import httpx2
import pytest

from brujula.core.config import Settings
from brujula.features.auth.oidc import GoogleOIDC, OIDCError, new_pkce_pair
from tests.support.google import FakeGoogle, new_key

pytestmark = pytest.mark.anyio

NONCE = "nonce-de-prueba"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def google(settings: Settings) -> FakeGoogle:
    return FakeGoogle(client_id=settings.google_client_id)


@pytest.fixture
async def oidc(settings: Settings, google: FakeGoogle) -> AsyncIterator[GoogleOIDC]:
    async with httpx2.AsyncClient(transport=google.transport()) as http:
        yield GoogleOIDC(settings, http)


async def test_token_valido_devuelve_la_identidad(oidc: GoogleOIDC, google: FakeGoogle) -> None:
    token = google.id_token(google.claims(nonce=NONCE, email="Ana@Ejemplo.com"))

    identity = await oidc.verify_id_token(token, nonce=NONCE)

    assert (identity.subject, identity.email, identity.name) == (
        "google-sub-1",
        "ana@ejemplo.com",
        "Ana Inversora",
    )


@pytest.mark.parametrize(
    ("overrides", "motivo"),
    [
        ({"aud": "otro-cliente"}, "aud"),
        ({"iss": "https://evil.example.com"}, "iss"),
        ({"nonce": "otro-nonce"}, "nonce"),
        ({"exp": int(time.time()) - 600}, "exp"),
        ({"email_verified": False}, "email_verified"),
        ({"email": None}, "email"),
    ],
)
async def test_claims_invalidos_se_rechazan(
    oidc: GoogleOIDC, google: FakeGoogle, overrides: dict[str, object], motivo: str
) -> None:
    claims = google.claims(**{"nonce": NONCE, **overrides})

    with pytest.raises(OIDCError, match="id_token_invalido"):
        await oidc.verify_id_token(google.id_token(claims), nonce=NONCE)


async def test_firma_de_otra_clave_se_rechaza(oidc: GoogleOIDC, google: FakeGoogle) -> None:
    impostor = new_key("clave-1")  # mismo kid, otra clave
    token = google.id_token(google.claims(nonce=NONCE), key=impostor)

    with pytest.raises(OIDCError, match="firma"):
        await oidc.verify_id_token(token, nonce=NONCE)


async def test_token_mal_formado_se_rechaza(oidc: GoogleOIDC) -> None:
    with pytest.raises(OIDCError):
        await oidc.verify_id_token("no.es.un-jwt", nonce=NONCE)


async def test_rotacion_de_claves_refresca_el_jwks(oidc: GoogleOIDC, google: FakeGoogle) -> None:
    await oidc.verify_id_token(google.id_token(google.claims(nonce=NONCE)), nonce=NONCE)
    google.key = new_key("clave-2")  # Google rota la clave

    identity = await oidc.verify_id_token(google.id_token(google.claims(nonce=NONCE)), nonce=NONCE)

    assert identity.subject == "google-sub-1"
    assert google.jwks_requests == 2


async def test_url_de_autorizacion_con_pkce(oidc: GoogleOIDC, settings: Settings) -> None:
    pkce = new_pkce_pair()

    url = await oidc.authorization_url(state="s", nonce="n", code_challenge=pkce.challenge)

    assert "code_challenge_method=S256" in url
    assert f"code_challenge={pkce.challenge}" in url
    assert "scope=openid+email+profile" in url
    assert f"client_id={settings.google_client_id}" in url
    assert 43 <= len(pkce.verifier) <= 128  # RFC 7636


async def test_canje_rechazado_por_google(oidc: GoogleOIDC, google: FakeGoogle) -> None:
    google.expected_challenge = "no-coincide"

    with pytest.raises(OIDCError, match="token_endpoint_rechazo:400"):
        await oidc.exchange_code(code="c", code_verifier="v" * 50)
