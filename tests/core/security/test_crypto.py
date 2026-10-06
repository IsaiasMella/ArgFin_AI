"""AES-256-GCM de campos: confidencialidad, integridad y rotación de claves (T1.2)."""

import os

import pytest

from brujula.core.security.crypto import DecryptionError, FieldCipher

CONTEXT = "holdings.cantidad"
OLD_KEY, NEW_KEY = os.urandom(32), os.urandom(32)


def test_ida_y_vuelta() -> None:
    cipher = FieldCipher(NEW_KEY)

    assert cipher.decrypt(cipher.encrypt(b"1234.5678", CONTEXT), CONTEXT) == b"1234.5678"


def test_el_valor_no_aparece_en_claro_y_cada_cifrado_es_distinto() -> None:
    cipher = FieldCipher(NEW_KEY)

    first, second = cipher.encrypt(b"1234.5678", CONTEXT), cipher.encrypt(b"1234.5678", CONTEXT)

    assert b"1234" not in first
    assert first != second  # nonce aleatorio: no se puede comparar valores cifrados


def test_valor_de_otra_columna_no_descifra() -> None:
    cipher = FieldCipher(NEW_KEY)
    blob = cipher.encrypt(b"100", "holdings.cantidad")

    with pytest.raises(DecryptionError, match="otra columna"):
        cipher.decrypt(blob, "holdings.precio_promedio")


def test_valor_alterado_no_descifra() -> None:
    cipher = FieldCipher(NEW_KEY)
    blob = bytearray(cipher.encrypt(b"100", CONTEXT))
    blob[-1] ^= 0x01

    with pytest.raises(DecryptionError):
        cipher.decrypt(bytes(blob), CONTEXT)


@pytest.mark.parametrize("blob", [b"", b"\x01abc", b"\x02" + b"0" * 40])
def test_formato_invalido(blob: bytes) -> None:
    with pytest.raises(DecryptionError, match="formato"):
        FieldCipher(NEW_KEY).decrypt(blob, CONTEXT)


def test_clave_no_configurada() -> None:
    blob = FieldCipher(OLD_KEY).encrypt(b"100", CONTEXT)

    with pytest.raises(DecryptionError, match="clave no configurada"):
        FieldCipher(NEW_KEY).decrypt(blob, CONTEXT)


def test_rotacion_lee_con_la_clave_anterior_y_recifra_con_la_nueva() -> None:
    old_blob = FieldCipher(OLD_KEY).encrypt(b"42.5", CONTEXT)
    rotating = FieldCipher(NEW_KEY, previous_keys=[OLD_KEY])

    assert rotating.decrypt(old_blob, CONTEXT) == b"42.5"
    assert rotating.needs_reencryption(old_blob)

    new_blob = rotating.reencrypt(old_blob, CONTEXT)

    assert not rotating.needs_reencryption(new_blob)
    assert FieldCipher(NEW_KEY).decrypt(new_blob, CONTEXT) == b"42.5"
