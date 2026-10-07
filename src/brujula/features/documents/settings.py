"""Lectura de `config/documents.yaml` (parámetros de la ingesta, versionados)."""

import re
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DOCUMENTS_FILE = "documents.yaml"

DocumentType = Literal[
    "estado_contable", "resena_informativa", "memoria_anual", "comunicado_resultados"
]


class DocumentsConfigError(Exception):
    """`config/documents.yaml` no existe o no cumple el esquema."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _compile(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"expresión regular inválida {pattern!r}: {exc}") from None


class CnvIngestion(_Strict):
    adjuntos: dict[str, DocumentType] = Field(min_length=1)
    ignorar_archivo: str
    hecho_relevante_resultados: str
    hecho_relevante_excluir: str

    @model_validator(mode="after")
    def _patterns(self) -> Self:
        for pattern in (
            self.ignorar_archivo,
            self.hecho_relevante_resultados,
            self.hecho_relevante_excluir,
        ):
            _compile(pattern)
        return self

    def ignores(self, filename: str) -> bool:
        return re.search(self.ignorar_archivo, filename) is not None

    def is_results_fact(self, description: str) -> bool:
        return re.search(self.hecho_relevante_resultados, description) is not None and (
            re.search(self.hecho_relevante_excluir, description) is None
        )


class SecIngestion(_Strict):
    comunicado_resultados: list[str] = Field(min_length=1)
    caracteres_a_revisar: Annotated[int, Field(gt=0)]
    extensiones: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _patterns(self) -> Self:
        for pattern in self.comunicado_resultados:
            _compile(pattern)
        return self

    def is_results_release(self, text: str) -> bool:
        head = text[: self.caracteres_a_revisar]
        return any(re.search(pattern, head) for pattern in self.comunicado_resultados)


class DocumentsConfig(_Strict):
    version: Literal[1]
    desde: date
    cnv: CnvIngestion
    sec: SecIngestion


def load_documents_config(path: Path) -> DocumentsConfig:
    try:
        return DocumentsConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except OSError as exc:
        raise DocumentsConfigError(f"no se pudo leer {path}: {exc.strerror}") from None
    except (yaml.YAMLError, ValidationError) as exc:
        raise DocumentsConfigError(f"{path} no es válido:\n{exc}") from None
