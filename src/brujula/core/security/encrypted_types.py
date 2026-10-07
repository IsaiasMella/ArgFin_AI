"""Tipo de columna cifrada para SQLAlchemy.

El cifrador se configura una vez al arrancar cada proceso (`configure_field_cipher`), porque
los modelos se definen al importar y la clave recién está disponible con `Settings`.
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Dialect, LargeBinary
from sqlalchemy.types import TypeDecorator

from brujula.core.config import Settings
from brujula.core.security.crypto import DecryptionError, FieldCipher, decode_key

_cipher: FieldCipher | None = None


def cipher_from_settings(settings: Settings) -> FieldCipher:
    previous = settings.field_encryption_keys_previous or []
    return FieldCipher(
        decode_key(settings.field_encryption_key.get_secret_value()),
        [decode_key(key.get_secret_value()) for key in previous],
    )


def configure_field_cipher(cipher: FieldCipher) -> None:
    global _cipher
    _cipher = cipher


def get_field_cipher() -> FieldCipher:
    if _cipher is None:
        raise RuntimeError("cifrado de campos sin configurar: llamá a configure_field_cipher")
    return _cipher


class EncryptedDecimal(TypeDecorator[Decimal]):
    """`Decimal` cifrado con AES-256-GCM, atado a su columna por `context`."""

    impl = LargeBinary
    cache_ok = True

    def __init__(self, context: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.context = context

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError(f"{self.context}: se esperaba un Decimal finito")
        return get_field_cipher().encrypt(format(value, "f").encode(), self.context)

    def process_result_value(self, value: bytes | None, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        plaintext = get_field_cipher().decrypt(bytes(value), self.context)
        try:
            return Decimal(plaintext.decode())
        except (InvalidOperation, UnicodeDecodeError):
            raise DecryptionError(f"{self.context}: contenido descifrado inválido") from None
