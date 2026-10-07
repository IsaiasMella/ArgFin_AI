"""Cliente OpenID Connect para Google: Authorization Code + PKCE (S256) + nonce.

Los endpoints salen del documento de descubrimiento (`GOOGLE_DISCOVERY_URL`), no están
escritos en el código. El ID token se valida por completo: firma (JWKS de Google),
emisor, audiencia, vencimiento, `nonce` y `email_verified`.
"""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx2
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from brujula.core.config import Settings

SCOPES = "openid email profile"
ID_TOKEN_ALGORITHMS = ["RS256"]
CLOCK_SKEW_SECONDS = 60
JWKS_TTL_SECONDS = 3600


class OIDCError(Exception):
    """El proveedor rechazó el flujo o el ID token no es válido. `reason` va a los logs."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ProviderMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str
    name: str | None


@dataclass(frozen=True)
class PKCEPair:
    verifier: str
    challenge: str


def new_pkce_pair() -> PKCEPair:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return PKCEPair(verifier=verifier, challenge=challenge)


class GoogleOIDC:
    provider = "google"

    def __init__(self, settings: Settings, http: httpx2.AsyncClient) -> None:
        self._http = http
        self._discovery_url = str(settings.google_discovery_url)
        self._client_id = settings.google_client_id
        self._client_secret = settings.google_client_secret
        self._redirect_uri = str(settings.google_redirect_uri)
        self._metadata: ProviderMetadata | None = None
        self._jwks: KeySet | None = None
        self._jwks_fetched_at = 0.0

    async def metadata(self) -> ProviderMetadata:
        if self._metadata is None:
            data = await self._get_json(self._discovery_url)
            try:
                self._metadata = ProviderMetadata(
                    issuer=data["issuer"],
                    authorization_endpoint=data["authorization_endpoint"],
                    token_endpoint=data["token_endpoint"],
                    jwks_uri=data["jwks_uri"],
                )
            except KeyError as exc:
                raise OIDCError(f"descubrimiento_incompleto:{exc.args[0]}") from None
        return self._metadata

    async def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        metadata = await self.metadata()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "scope": SCOPES,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{metadata.authorization_endpoint}?{query}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> str:
        """Canjea el código por tokens (de servidor a servidor) y devuelve el ID token."""
        metadata = await self.metadata()
        try:
            response = await self._http.post(
                metadata.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": code_verifier,
                    "redirect_uri": self._redirect_uri,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret.get_secret_value(),
                },
            )
        except httpx2.HTTPError:
            raise OIDCError("token_endpoint_inaccesible") from None
        if response.status_code != httpx2.codes.OK:
            raise OIDCError(f"token_endpoint_rechazo:{response.status_code}")
        id_token = response.json().get("id_token")
        if not isinstance(id_token, str):
            raise OIDCError("respuesta_sin_id_token")
        return id_token

    async def verify_id_token(self, id_token: str, *, nonce: str) -> Identity:
        metadata = await self.metadata()
        token = await self._decode(id_token)
        issuers = [metadata.issuer, metadata.issuer.removeprefix("https://")]
        registry = JWTClaimsRegistry(
            leeway=CLOCK_SKEW_SECONDS,
            iss={"essential": True, "values": issuers},
            aud={"essential": True, "value": self._client_id},
            sub={"essential": True},
            exp={"essential": True},
            nonce={"essential": True, "value": nonce},
            email={"essential": True},
            email_verified={"essential": True, "value": True},
        )
        try:
            registry.validate(token.claims)
        except JoseError as exc:
            raise OIDCError(f"id_token_invalido:{exc.error}") from None
        claims = token.claims
        return Identity(
            subject=str(claims["sub"]),
            email=str(claims["email"]).lower(),
            name=str(claims["name"]) if claims.get("name") else None,
        )

    async def _decode(self, id_token: str) -> jwt.Token:
        for refresh in (False, True):
            keys = await self._key_set(force_refresh=refresh)
            try:
                return jwt.decode(id_token, keys, algorithms=ID_TOKEN_ALGORITHMS)
            except (JoseError, ValueError) as exc:
                # Google rota sus claves: si el `kid` no está, se reintenta con el JWKS nuevo.
                unknown_key = "key" in str(exc).lower() and not refresh
                if not unknown_key:
                    raise OIDCError(f"id_token_firma_invalida:{type(exc).__name__}") from None
        raise OIDCError("id_token_firma_invalida")  # pragma: no cover - el bucle siempre sale

    async def _key_set(self, *, force_refresh: bool) -> KeySet:
        expired = time.monotonic() - self._jwks_fetched_at > JWKS_TTL_SECONDS
        if self._jwks is None or expired or force_refresh:
            metadata = await self.metadata()
            data = await self._get_json(metadata.jwks_uri)
            try:
                self._jwks = KeySet.import_key_set(data)  # type: ignore[arg-type]
            except (JoseError, ValueError, KeyError):
                raise OIDCError("jwks_invalido") from None
            self._jwks_fetched_at = time.monotonic()
        return self._jwks

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            response = await self._http.get(url)
            response.raise_for_status()
            data = response.json()
        except (httpx2.HTTPError, ValueError):
            raise OIDCError(f"no_se_pudo_obtener:{url}") from None
        if not isinstance(data, dict):
            raise OIDCError(f"respuesta_inesperada:{url}")
        return data
