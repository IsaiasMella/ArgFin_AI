"""Del plan de cuentas de la CNV a métricas internas (T3.4, ADR 016). Sin LLM.

- Los montos se leen con reglas estrictas: un formato ambiguo (p. ej. "1.234" con un solo
  punto y tres decimales) no se adivina, se informa como error.
- Los importes se llevan a unidades con la `UnidadMedida` de la presentación ("Miles de $").
- Los estados argentinos están en moneda homogénea (NIC 29): reexpresados al cierre.
- Los flujos de un estado trimestral son acumulados del ejercicio: empiezan el día
  siguiente al cierre del ejercicio anterior.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from brujula.features.financials.catalog import FinancialsConfigError, MetricCatalog

CNV_ACCOUNTS_FILE = "cnv_accounts.yaml"
UNIT_MULTIPLIERS = {"$": Decimal(1), "miles de $": Decimal(1000), "millones de $": Decimal(10**6)}
_PLAIN = re.compile(r"^-?\d+(\.\d+)?$")
_THOUSANDS_DOTS = re.compile(r"^-?\d{1,3}(\.\d{3}){2,}$")
_AMBIGUOUS = re.compile(r"^-?\d{1,3}\.\d{3}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AccountRef(_Strict):
    nro: str
    rubro: str


class AccountRule(_Strict):
    metrica: str
    nro: str | None = None
    rubro: str | None = None
    suma: list[AccountRef] | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        single = self.nro is not None and self.rubro is not None
        if single == (self.suma is not None):
            raise ValueError(f"{self.metrica}: va nro y rubro, o suma, no ambos")
        for pattern in [self.rubro or "", *(ref.rubro for ref in self.suma or [])]:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"{self.metrica}: patrón inválido: {exc}") from None
        return self

    def refs(self) -> list[AccountRef]:
        if self.suma is not None:
            return self.suma
        return [AccountRef(nro=self.nro or "", rubro=self.rubro or "")]


class CnvAccountMap(_Strict):
    version: Literal[1]
    reglas: list[AccountRule] = Field(min_length=1)


def load_cnv_accounts(config_dir: Path, catalog: MetricCatalog) -> CnvAccountMap:
    path = config_dir / CNV_ACCOUNTS_FILE
    try:
        accounts = CnvAccountMap.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except OSError as exc:
        raise FinancialsConfigError(f"no se pudo leer {path}: {exc.strerror}") from None
    except (yaml.YAMLError, ValidationError) as exc:
        raise FinancialsConfigError(f"{path} no es válido:\n{exc}") from None
    unknown = sorted({r.metrica for r in accounts.reglas} - set(catalog.metricas))
    if unknown:
        raise FinancialsConfigError(f"métricas fuera del catálogo: {', '.join(unknown)}")
    return accounts


class AmountError(ValueError):
    """Monto no numérico o con un formato ambiguo."""


class EmptyAmountError(AmountError):
    """La cuenta no tiene saldo ("-" o vacío): la métrica no se informa."""


def normalize_label(text: str) -> str:
    """Mayúsculas, sin tildes, espacios simples. Los caracteres ilegibles se descartan."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain).strip().upper()


def parse_amount(text: str) -> Decimal:
    value = text.strip().replace(" ", "")
    if value in {"", "-"}:
        raise EmptyAmountError("vacío")
    if _AMBIGUOUS.match(value):
        raise AmountError(f"formato ambiguo: {text!r}")
    if _THOUSANDS_DOTS.match(value):
        value = value.replace(".", "")
    if not _PLAIN.match(value):
        raise AmountError(f"no numérico: {text!r}")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise AmountError(f"no numérico: {text!r}") from None


def unit_multiplier(unit: str | None) -> Decimal:
    key = re.sub(r"\s+", " ", (unit or "").strip().lower())
    if key not in UNIT_MULTIPLIERS:
        raise AmountError(f"unidad desconocida: {unit!r}")
    return UNIT_MULTIPLIERS[key]


def fiscal_year_start(period_end: date, fiscal_year_end: str) -> date:
    """Primer día del ejercicio que contiene `period_end` (`fiscal_year_end` es "MM-DD")."""
    month, day = (int(part) for part in fiscal_year_end.split("-"))
    year_end = date(period_end.year, month, day)
    if year_end >= period_end:
        year_end = date(period_end.year - 1, month, day)
    return year_end + timedelta(days=1)


@dataclass(frozen=True)
class CnvFact:
    metrica: str
    periodo_inicio: date | None
    periodo_fin: date
    valor: Decimal
    unidad: str
    # Montos tal como figuran en la presentación (antes de aplicar la unidad).
    montos_originales: tuple[str, ...]
    cuentas: tuple[str, ...]


@dataclass
class CnvMapping:
    facts: list[CnvFact] = field(default_factory=list)
    # (métrica, motivo) de lo que no se pudo leer
    errores: list[tuple[str, str]] = field(default_factory=list)


def map_statement(
    cuentas: list[dict[str, Any]],
    *,
    unidad: str | None,
    fecha_cierre: date,
    cierre_ejercicio: str,
    accounts: CnvAccountMap,
    catalog: MetricCatalog,
) -> CnvMapping:
    result = CnvMapping()
    try:
        multiplier = unit_multiplier(unidad)
    except AmountError as exc:
        result.errores.append(("*", str(exc)))
        return result
    rows = [
        (str(c.get("nro", "")), normalize_label(str(c.get("rubro", ""))), str(c.get("monto", "")))
        for c in cuentas
    ]
    start = fiscal_year_start(fecha_cierre, cierre_ejercicio)
    for rule in accounts.reglas:
        metric = catalog.metricas[rule.metrica]
        amounts: list[str] = []
        try:
            for ref in rule.refs():
                matches = [
                    m for nro, rubro, m in rows if nro == ref.nro and re.search(ref.rubro, rubro)
                ]
                if not matches:
                    raise LookupError
                amounts.append(matches[0])
            values = [parse_amount(amount) for amount in amounts]
        except (LookupError, EmptyAmountError):
            continue  # la plantilla no tiene la cuenta, o la cuenta no tiene saldo
        except AmountError as exc:
            result.errores.append((rule.metrica, str(exc)))
            continue
        total = sum(values, Decimal(0))
        scaled = total * multiplier if metric.unidad == "moneda" else total
        result.facts.append(
            CnvFact(
                metrica=rule.metrica,
                periodo_inicio=start if metric.tipo == "flujo" else None,
                periodo_fin=fecha_cierre,
                valor=scaled,
                unidad=metric.unidad,
                montos_originales=tuple(amounts),
                cuentas=tuple(ref.nro for ref in rule.refs()),
            )
        )
    return result
