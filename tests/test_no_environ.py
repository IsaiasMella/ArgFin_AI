"""Ningún módulo lee variables de entorno fuera de `core/config.py` (T0.2)."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "brujula"
ALLOWED = {SRC / "core" / "config.py"}
FORBIDDEN_OS_ATTRS = {"environ", "environb", "getenv", "getenvb", "putenv", "unsetenv"}


def environ_uses(source: str) -> list[int]:
    """Líneas que acceden al entorno del proceso vía `os` (con o sin alias)."""
    tree = ast.parse(source)
    os_aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "os"
    }
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "os"
            and any(alias.name in FORBIDDEN_OS_ATTRS | {"*"} for alias in node.names)
        ) or (
            isinstance(node, ast.Attribute)
            and node.attr in FORBIDDEN_OS_ATTRS
            and isinstance(node.value, ast.Name)
            and node.value.id in os_aliases
        ):
            lines.append(node.lineno)
    return lines


@pytest.mark.parametrize(
    "source",
    [
        "import os\nos.environ['X']",
        "import os\nos.getenv('X')",
        "import os as sistema\nsistema.environ.get('X')",
        "from os import environ",
        "from os import getenv as leer",
        "from os import *",
    ],
)
def test_detector_encuentra_accesos(source: str) -> None:
    assert environ_uses(source)


@pytest.mark.parametrize(
    "source", ["import os\nos.path.join('a')", "from os import path", "environ = {}\nenviron['X']"]
)
def test_detector_ignora_otros_usos(source: str) -> None:
    assert not environ_uses(source)


def test_solo_config_lee_el_entorno() -> None:
    offenders = [
        f"{path.relative_to(SRC.parent)}:{line}"
        for path in sorted(SRC.rglob("*.py"))
        if path not in ALLOWED
        for line in environ_uses(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], "Leé la configuración con get_settings(): " + ", ".join(offenders)
