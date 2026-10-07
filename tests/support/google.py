"""Google simulado para tests: descubrimiento OIDC, JWKS, endpoint de tokens e ID tokens
firmados con una clave RSA generada en el momento."""

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx2
from joserfc import jwt
from joserfc.jwk import RSAKey

ISSUER = "https://accounts.google.com"
DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 (URL, no es un secreto)
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"


def new_key(kid: str) -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": kid, "alg": "RS256", "use": "sig"})


@dataclass
class FakeGoogle:
    client_id: str
    key: RSAKey = field(default_factory=lambda: new_key("clave-1"))
    # Claims del próximo ID token que entregue el endpoint de tokens (los completa el test).
    next_claims: dict[str, Any] = field(default_factory=dict)
    signing_key: RSAKey | None = None
    expected_challenge: str | None = None
    jwks_requests: int = 0
    token_requests: list[dict[str, str]] = field(default_factory=list)

    def claims(self, **overrides: Any) -> dict[str, Any]:
        now = int(time.time())
        base: dict[str, Any] = {
            "iss": ISSUER,
            "aud": self.client_id,
            "sub": "google-sub-1",
            "email": "ana@ejemplo.com",
            "email_verified": True,
            "name": "Ana Inversora",
            "iat": now,
            "exp": now + 3600,
        }
        base.update(overrides)
        return {k: v for k, v in base.items() if v is not None}

    def id_token(self, claims: dict[str, Any], key: RSAKey | None = None) -> str:
        signer = key or self.signing_key or self.key
        header = {"alg": "RS256", "kid": signer.kid}
        return jwt.encode(header, claims, signer)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url).split("?")[0]
        if url == DISCOVERY_URL:
            return httpx2.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": AUTHORIZATION_ENDPOINT,
                    "token_endpoint": TOKEN_ENDPOINT,
                    "jwks_uri": JWKS_URI,
                },
            )
        if url == JWKS_URI:
            self.jwks_requests += 1
            return httpx2.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        if url == TOKEN_ENDPOINT:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            if self.expected_challenge is not None:
                digest = hashlib.sha256(form["code_verifier"].encode()).digest()
                challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
                if challenge != self.expected_challenge:
                    return httpx2.Response(400, json={"error": "invalid_grant"})
            return httpx2.Response(200, json={"id_token": self.id_token(self.next_claims)})
        return httpx2.Response(404, content=json.dumps({"error": "not_found"}))

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handler)
