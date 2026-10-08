"""Resultado de una corrida contra una fuente externa (monitor de integraciones, T3.7)."""

from dataclasses import dataclass

from brujula.core.http import FetchError, FormatError


@dataclass(frozen=True)
class SourceRun:
    """Una consulta a una fuente: bien (`motivo` None) o con su motivo de falla.

    `objeto` es la empresa o especie consultada ("" si la corrida abarca toda la fuente).
    `formato` distingue un cambio de formato de la fuente (se avisa enseguida) de una falla
    que puede ser transitoria (se avisa si se repite).
    """

    fuente: str
    objeto: str = ""
    motivo: str | None = None
    formato: bool = False

    @property
    def ok(self) -> bool:
        return self.motivo is None

    @classmethod
    def from_error(cls, fuente: str, objeto: str, exc: FetchError) -> "SourceRun":
        return cls(fuente, objeto, exc.reason, isinstance(exc, FormatError))
