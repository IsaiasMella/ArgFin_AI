"""Protección CSRF para métodos que modifican estado (plan técnico, §6.4).

Dos controles, ambos obligatorios:
1. `Origin` (o `Referer` si falta) debe ser el frontend o la propia API.
2. Doble envío firmado: el header `X-CSRF-Token` debe coincidir con
   HMAC(CSRF_SECRET, token de sesión). El frontend lo lee de la cookie `brujula_csrf`.
   Como depende del secreto, una cookie plantada desde otro subdominio no sirve.
"""

import hashlib
import hmac
from urllib.parse import urlsplit

CSRF_HEADER = "X-CSRF-Token"


def csrf_token(csrf_secret: str, session_token: str) -> str:
    return hmac.new(csrf_secret.encode(), session_token.encode(), hashlib.sha256).hexdigest()


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def origin_allowed(origin: str | None, referer: str | None, allowed: set[str]) -> bool:
    source = origin or (origin_of(referer) if referer else None)
    return source is not None and source.lower() in allowed


def csrf_valid(header_value: str | None, csrf_secret: str, session_token: str) -> bool:
    if not header_value:
        return False
    return hmac.compare_digest(header_value, csrf_token(csrf_secret, session_token))
