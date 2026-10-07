"""Almacenamiento en disco por hash (T3.2)."""

import hashlib
from pathlib import Path

import anyio
import pytest

from brujula.features.documents.storage import DiskStorage

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_guarda_por_hash_y_lee_lo_mismo(tmp_path: Path) -> None:
    storage = DiskStorage(tmp_path)

    stored = await storage.save(b"%PDF balance", ".PDF")

    digest = hashlib.sha256(b"%PDF balance").hexdigest()
    assert (stored.sha256, stored.path, stored.size) == (digest, f"{digest[:2]}/{digest}.pdf", 12)
    assert await storage.read(stored.path) == b"%PDF balance"


async def test_el_mismo_contenido_se_guarda_una_sola_vez(tmp_path: Path) -> None:
    storage = DiskStorage(tmp_path)

    first = await storage.save(b"igual", "pdf")
    second = await storage.save(b"igual", ".pdf")

    assert first == second
    files = [path async for path in anyio.Path(tmp_path).rglob("*") if await path.is_file()]
    assert [path.name for path in files] == [f"{first.sha256}.pdf"]


async def test_no_lee_fuera_de_su_carpeta(tmp_path: Path) -> None:
    storage = DiskStorage(tmp_path / "docs")
    (tmp_path / "secreto.txt").write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="fuera del almacenamiento"):
        await storage.read("../secreto.txt")
