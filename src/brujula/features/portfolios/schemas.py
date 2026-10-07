"""Esquemas de entrada y salida del portafolio. Toda entrada se valida estrictamente."""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from brujula.core.tickers import Ticker

BROKER_MAX_LENGTH = 60


def _normalize_broker(value: str | None) -> str | None:
    if value is None:
        return None
    broker = value.strip()
    return broker or None


Quantity = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=8)]
Price = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=6)]
Broker = Annotated[
    str | None, Field(max_length=BROKER_MAX_LENGTH), AfterValidator(_normalize_broker)
]
Currency = Literal["ARS", "USD"]


class Coverage(StrEnum):
    FULL = "completa"
    PRICE_ONLY = "solo_precio"


def _price_needs_currency(price: Decimal | None, currency: str | None) -> None:
    if (price is None) != (currency is None):
        raise ValueError("precio_promedio y moneda_precio van juntos (o ninguno)")


class HoldingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: Ticker
    cantidad: Quantity
    precio_promedio: Price | None = None
    moneda_precio: Currency | None = None
    broker: Broker = None

    @model_validator(mode="after")
    def _check_price(self) -> Self:
        _price_needs_currency(self.precio_promedio, self.moneda_precio)
        return self


class HoldingUpdate(BaseModel):
    """Cambia cantidad, precio o broker. El ticker no cambia: se borra y se carga otra."""

    model_config = ConfigDict(extra="forbid")

    cantidad: Quantity | None = None
    precio_promedio: Price | None = None
    moneda_precio: Currency | None = None
    broker: Broker = None

    @model_validator(mode="after")
    def _check_price(self) -> Self:
        if "precio_promedio" in self.model_fields_set or "moneda_precio" in self.model_fields_set:
            _price_needs_currency(self.precio_promedio, self.moneda_precio)
        return self


class HoldingOut(BaseModel):
    id: UUID
    ticker: str
    empresa: str | None
    cobertura: Coverage
    cantidad: Decimal
    precio_promedio: Decimal | None
    moneda_precio: Currency | None
    broker: str | None


class CsvRowError(BaseModel):
    fila: int
    errores: list[str]


class CsvImportResult(BaseModel):
    filas_procesadas: int
    importadas: list[HoldingOut]
    errores: list[CsvRowError]
