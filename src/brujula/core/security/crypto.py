"""Cifrado de campos con AES-256-GCM (constitución, punto 6). Ver docs/adr/008.

Formato de cada valor cifrado (bytes):

    versión (1) | id de clave (4) | nonce (12) | texto cifrado + tag GCM (16)

- El **id de clave** (primeros 4 bytes del SHA-256 de la clave) permite descifrar con una
  clave anterior durante una rotación y saber qué filas faltan re-cifrar.
- Los **datos asociados** (AAD) atan el valor a su columna (`holdings.cantidad`): un valor
  copiado a otra columna no descifra.
- El nonce es aleatorio por valor (96 bits); con este volumen el riesgo de colisión es nulo.
"""

import base64
import hashlib
import os
from collections.abc import Sequence

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT_VERSION = 1
KEY_ID_LENGTH = 4
NONCE_LENGTH = 12
HEADER_LENGTH = 1 + KEY_ID_LENGTH + NONCE_LENGTH


class DecryptionError(ValueError):
    """El valor está corrupto, fue movido de columna o su clave ya no está configurada."""


def _key_id(key: bytes) -> bytes:
    return hashlib.sha256(key).digest()[:KEY_ID_LENGTH]


def decode_key(encoded: str) -> bytes:
    return base64.urlsafe_b64decode(encoded.encode("ascii"))


class FieldCipher:
    def __init__(self, primary_key: bytes, previous_keys: Sequence[bytes] = ()) -> None:
        self._primary_id = _key_id(primary_key)
        self._primary = AESGCM(primary_key)
        self._keys = {_key_id(key): AESGCM(key) for key in (*previous_keys, primary_key)}

    def encrypt(self, plaintext: bytes, context: str) -> bytes:
        nonce = os.urandom(NONCE_LENGTH)
        header = bytes([FORMAT_VERSION]) + self._primary_id + nonce
        return header + self._primary.encrypt(nonce, plaintext, _aad(context))

    def decrypt(self, blob: bytes, context: str) -> bytes:
        if len(blob) <= HEADER_LENGTH or blob[0] != FORMAT_VERSION:
            raise DecryptionError("formato de valor cifrado desconocido")
        key_id = blob[1 : 1 + KEY_ID_LENGTH]
        cipher = self._keys.get(key_id)
        if cipher is None:
            raise DecryptionError("el valor está cifrado con una clave no configurada")
        nonce = blob[1 + KEY_ID_LENGTH : HEADER_LENGTH]
        try:
            return cipher.decrypt(nonce, blob[HEADER_LENGTH:], _aad(context))
        except InvalidTag:
            raise DecryptionError("valor cifrado inválido o de otra columna") from None

    def needs_reencryption(self, blob: bytes) -> bool:
        """True si el valor está cifrado con una clave que no es la actual."""
        return blob[1 : 1 + KEY_ID_LENGTH] != self._primary_id

    def reencrypt(self, blob: bytes, context: str) -> bytes:
        return self.encrypt(self.decrypt(blob, context), context)


def _aad(context: str) -> bytes:
    return f"brujula:{context}".encode()
