"""Hashes con clave para no guardar ni loguear identificadores en claro.

- `token_hash`: lo único que se guarda de un token de sesión (si se filtra la base, los
  tokens no sirven).
- `pseudonymize`: id estable para logs y métricas (IP, user_id) sin exponer el valor. La
  clave se deriva de `SESSION_SECRET` con un propósito, así cada uso tiene su propia clave.
"""

import hashlib
import hmac


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def derive_key(secret: str, purpose: str) -> bytes:
    return hmac.new(secret.encode(), purpose.encode(), hashlib.sha256).digest()


def pseudonymize(value: str, key: bytes) -> str:
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:16]
