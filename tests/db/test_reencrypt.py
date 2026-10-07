"""Comando `recifrar`: paso 3 de la rotación de la clave de cifrado (ADR 008)."""

import base64
import os

import psycopg
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine

from brujula.cli import reencrypt_holdings
from brujula.core.config import Settings
from brujula.core.security.crypto import DecryptionError
from brujula.core.security.encrypted_types import cipher_from_settings
from tests.db.conftest import Database, make_client
from tests.support.app import csrf_headers, login
from tests.support.google import FakeGoogle


def _key() -> SecretStr:
    return SecretStr(base64.urlsafe_b64encode(os.urandom(32)).decode())


def test_rotacion_recifra_y_conserva_los_valores(
    auth_settings: Settings,
    google: FakeGoogle,
    database: Database,
    superuser: psycopg.Connection,
) -> None:
    old_key, new_key = _key(), _key()
    before = auth_settings.model_copy(update={"field_encryption_key": old_key})
    with make_client(before, google) as client:
        login(client, google)
        for ticker in ("AAA", "BBB"):
            client.post(
                "/portfolio/holdings",
                json={
                    "ticker": ticker,
                    "cantidad": "12.5",
                    "precio_promedio": "99",
                    "moneda_precio": "USD",
                },
                headers=csrf_headers(client),
            )

    rotating = auth_settings.model_copy(
        update={"field_encryption_key": new_key, "field_encryption_keys_previous": [old_key]}
    )
    cipher = cipher_from_settings(rotating)
    engine = create_engine(database.url("migrator"))
    try:
        first = reencrypt_holdings(engine, cipher)
        second = reencrypt_holdings(engine, cipher)
    finally:
        engine.dispose()

    assert (first.revisadas, first.recifradas) == (2, 2)
    assert (second.revisadas, second.recifradas) == (2, 0)  # idempotente
    blobs = superuser.execute("SELECT cantidad_cifrada FROM holdings").fetchall()
    assert not any(cipher.needs_reencryption(blob) for (blob,) in blobs)

    # Con solo la clave nueva (la vieja ya retirada) todo se sigue leyendo.
    after = auth_settings.model_copy(update={"field_encryption_key": new_key})
    with make_client(after, google) as client:
        login(client, google)
        holdings = client.get("/portfolio/holdings").json()
    assert [(h["cantidad"], h["precio_promedio"]) for h in holdings] == [("12.5", "99")] * 2


def test_sin_la_clave_correcta_falla_en_lugar_de_devolver_basura(
    auth_settings: Settings, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    with make_client(auth_settings, google) as client:
        login(client, google)
        client.post(
            "/portfolio/holdings",
            json={"ticker": "AAA", "cantidad": "1"},
            headers=csrf_headers(client),
        )
    other = auth_settings.model_copy(update={"field_encryption_key": _key()})
    with make_client(other, google) as client:
        login(client, google)
        with pytest.raises(DecryptionError, match="clave no configurada"):
            client.get("/portfolio/holdings")
