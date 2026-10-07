"""Almacenamiento de documentos (plan técnico, §3). Hoy en disco; migrable a S3 sin tocar
las features, porque todo pasa por `DocumentStorage`."""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import anyio


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    path: str  # relativa a la raíz del almacenamiento
    size: int


class DocumentStorage(Protocol):
    async def save(self, content: bytes, extension: str) -> StoredFile: ...

    async def read(self, path: str) -> bytes: ...


class DiskStorage:
    """Guarda cada archivo por su hash: el mismo contenido nunca se guarda dos veces."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _absolute(self, relative: str) -> Path:
        path = (self._root / relative).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("ruta fuera del almacenamiento")
        return path

    async def save(self, content: bytes, extension: str) -> StoredFile:
        digest = hashlib.sha256(content).hexdigest()
        suffix = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        relative = f"{digest[:2]}/{digest}{suffix}"
        target = anyio.Path(self._absolute(relative))
        if not await target.exists():
            await target.parent.mkdir(parents=True, exist_ok=True)
            # Escritura atómica: un corte a mitad de camino no deja un archivo truncado.
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            await temporary.write_bytes(content)
            await temporary.replace(target)
        return StoredFile(sha256=digest, path=relative, size=len(content))

    async def read(self, path: str) -> bytes:
        return await anyio.Path(self._absolute(path)).read_bytes()
