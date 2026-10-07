"""Lectura y validación de `config/universe.yaml` (T2.1).

El archivo es la fuente de verdad del universo cubierto; la base es una copia que se actualiza
con `python -m brujula.cli sincronizar-universo`.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from brujula.core.tickers import Ticker

UNIVERSE_FILE = "universe.yaml"

Key = Annotated[str, Field(pattern=r"^[A-Z0-9_]{2,40}$")]
CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
Cik = Annotated[str, Field(pattern=r"^\d{10}$")]
Text = Annotated[str, Field(min_length=1, max_length=200)]


class UniverseError(Exception):
    """El archivo del universo no existe, no es YAML o no cumple el esquema."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Window(_Strict):
    desde: date
    hasta: date
    descripcion: str

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.desde > self.hasta:
            raise ValueError("la ventana termina antes de empezar")
        return self


class Selection(_Strict):
    """Cómo se eligió el universo: queda documentado junto a los datos."""

    fecha_de_corte: date
    ventana: Window
    fuente: str
    metrica: str
    criterio_acciones: str
    criterio_cedears: str
    excluidos: dict[str, str] = Field(default_factory=dict)
    fuentes_de_datos: dict[str, str]
    ranking_millones_ars: dict[str, dict[str, int]]


class InstrumentEntry(_Strict):
    ticker_byma: Ticker
    ticker_origen: Ticker | None = None
    ratio_cedear: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=4)] | None = None
    moneda: Literal["ARS", "USD"]


class CompanyEntry(_Strict):
    clave: Key
    nombre: Text
    tipo: Literal["ar_equity", "cedear"]
    sector: Text
    pais: CountryCode
    cik_sec: Cik | None = None
    url_relacion_inversores: str | None = None
    instrumentos: list[InstrumentEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _ratio_matches_type(self) -> Self:
        for instrument in self.instrumentos:
            if self.tipo == "cedear" and (
                instrument.ratio_cedear is None or instrument.ticker_origen is None
            ):
                raise ValueError(f"{instrument.ticker_byma}: un CEDEAR necesita ratio y subyacente")
            if self.tipo == "ar_equity" and instrument.ratio_cedear is not None:
                raise ValueError(f"{instrument.ticker_byma}: una acción local no lleva ratio")
        return self


class Universe(_Strict):
    version: Literal[1]
    seleccion: Selection
    empresas: list[CompanyEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_keys(self) -> Self:
        keys = [company.clave for company in self.empresas]
        tickers = [i.ticker_byma for company in self.empresas for i in company.instrumentos]
        for label, values in (("claves", keys), ("tickers", tickers)):
            repeated = sorted({value for value in values if values.count(value) > 1})
            if repeated:
                raise ValueError(f"{label} repetidas: {', '.join(repeated)}")
        return self

    def count(self, tipo: str) -> int:
        return sum(1 for company in self.empresas if company.tipo == tipo)


def load_universe(path: Path) -> Universe:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UniverseError(f"no se pudo leer {path}: {exc.strerror}") from None
    except yaml.YAMLError as exc:
        raise UniverseError(f"{path} no es YAML válido: {exc}") from None
    try:
        return Universe.model_validate(raw)
    except ValidationError as exc:
        raise UniverseError(f"{path} no cumple el esquema:\n{exc}") from None
