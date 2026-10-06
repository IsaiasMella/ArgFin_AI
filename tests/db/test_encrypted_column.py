"""En la base no aparece ningún valor en claro (criterio de T1.2)."""

import os
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from brujula.core.security import encrypted_types
from brujula.core.security.crypto import FieldCipher
from brujula.core.security.encrypted_types import EncryptedDecimal, configure_field_cipher
from tests.db.conftest import Database

pytestmark = pytest.mark.anyio

metadata = MetaData()
prueba = Table(
    "prueba_cifrado",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("cantidad", EncryptedDecimal("prueba.cantidad")),
    prefixes=["TEMPORARY"],
)


@pytest.fixture
async def engine(database: Database) -> AsyncIterator[AsyncEngine]:
    configure_field_cipher(FieldCipher(os.urandom(32)))
    engine = create_async_engine(database.url("superuser"), pool_size=1, max_overflow=0)
    yield engine
    await engine.dispose()
    encrypted_types._cipher = None


async def test_la_base_guarda_solo_bytes_cifrados(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.execute(insert(prueba).values(id=1, cantidad=Decimal("98765.4321")))

        raw = await conn.scalar(text("SELECT cantidad FROM prueba_cifrado WHERE id = 1"))
        decoded = await conn.scalar(select(prueba.c.cantidad).where(prueba.c.id == 1))

    assert isinstance(raw, bytes)
    assert b"98765" not in raw
    assert "98765" not in raw.hex()
    assert decoded == Decimal("98765.4321")
