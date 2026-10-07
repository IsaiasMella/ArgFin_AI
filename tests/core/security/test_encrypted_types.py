"""Tipo de columna `EncryptedDecimal` (T1.2)."""

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql

from brujula.core.security import encrypted_types
from brujula.core.security.crypto import FieldCipher
from brujula.core.security.encrypted_types import EncryptedDecimal, configure_field_cipher

DIALECT = postgresql.dialect()  # type: ignore[no-untyped-call]


@pytest.fixture(autouse=True)
def cipher() -> Iterator[None]:
    configure_field_cipher(FieldCipher(os.urandom(32)))
    yield
    encrypted_types._cipher = None


@pytest.mark.parametrize("value", [Decimal("1234.5678"), Decimal("1E+2"), Decimal("0.000001")])
def test_ida_y_vuelta_conserva_el_valor(value: Decimal) -> None:
    column = EncryptedDecimal("holdings.cantidad")

    stored = column.process_bind_param(value, DIALECT)

    assert stored is not None
    assert format(value, "f").encode() not in stored
    assert column.process_result_value(stored, DIALECT) == value


def test_null_queda_null() -> None:
    column = EncryptedDecimal("holdings.precio_promedio")

    assert column.process_bind_param(None, DIALECT) is None
    assert column.process_result_value(None, DIALECT) is None


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), 3.5])
def test_rechaza_valores_no_decimales_finitos(value: object) -> None:
    with pytest.raises(ValueError, match="Decimal finito"):
        EncryptedDecimal("holdings.cantidad").process_bind_param(value, DIALECT)  # type: ignore[arg-type]


def test_sin_configurar_falla_explicitamente() -> None:
    encrypted_types._cipher = None

    with pytest.raises(RuntimeError, match="sin configurar"):
        EncryptedDecimal("holdings.cantidad").process_bind_param(Decimal(1), DIALECT)
