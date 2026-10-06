"""Registro de prompts versionados.

Cada prompt es un archivo `PROMPTS_DIR/<nombre>@<versión>.yaml`:

    descripcion: Qué hace el prompt.
    sistema: |
      Instrucciones de sistema.
    usuario: |
      Texto con variables ${asi}.
    parametros:        # opcional: se pasan tal cual al proveedor
      max_tokens: 2000

Una versión publicada no se edita: un cambio es una versión nueva, y cada resultado guarda
la referencia `nombre@versión` que lo produjo.
"""

import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from string import Template
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROMPT_REF = re.compile(r"^(?P<name>[a-z0-9_]+)@(?P<version>[0-9]+)$")


class PromptError(ValueError):
    """Prompt inexistente, mal formado o con variables faltantes."""


class PromptFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    descripcion: str = Field(min_length=1)
    sistema: str = Field(min_length=1)
    usuario: str = Field(min_length=1)
    parametros: dict[str, Any] = Field(default_factory=dict)


class Prompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    system: str
    user_template: str
    params: dict[str, Any]

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def render_user(self, variables: Mapping[str, str]) -> str:
        try:
            return Template(self.user_template).substitute(variables)
        except KeyError as exc:
            raise PromptError(f"{self.ref}: falta la variable {exc.args[0]!r}") from None
        except ValueError as exc:
            raise PromptError(f"{self.ref}: plantilla inválida ({exc})") from None


class PromptRegistry:
    def __init__(self, prompts_dir: Path) -> None:
        self._dir = prompts_dir

    def get(self, ref: str) -> Prompt:
        match = PROMPT_REF.match(ref)
        if match is None:
            raise PromptError(f"referencia inválida {ref!r}: se espera nombre@versión")
        return _load(self._dir / f"{ref}.yaml", match["name"], match["version"])


@cache
def _load(path: Path, name: str, version: str) -> Prompt:
    if not path.is_file():
        raise PromptError(f"no existe el prompt {path.name} en {path.parent}")
    try:
        data = PromptFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (yaml.YAMLError, ValidationError) as exc:
        raise PromptError(f"{path.name} está mal formado: {exc}") from None
    return Prompt(
        name=name,
        version=version,
        system=data.sistema,
        user_template=data.usuario,
        params=data.parametros,
    )
