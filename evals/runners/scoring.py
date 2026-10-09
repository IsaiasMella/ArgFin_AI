"""Dataset de referencia y puntaje de la extracción de estados contables (T3.6, ADR 018).

Funciones puras, sin E/S: el runner (`extraction.py`) lee los documentos y llama al LLM.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from brujula.features.financials.catalog import MetricCatalog
from brujula.features.financials.cnv_mapping import CnvFact
from brujula.features.financials.schemas import DocumentUnit, FinancialStatementExtraction
from brujula.features.financials.verification import (
    DOCUMENT_UNITS,
    MIN_SCALED_DIGITS,
    pages_with,
    printed_forms,
)

EVALS_FILE = "evals.yaml"

Gate = Literal["cumple", "no_cumple", "no_evaluable"]


class EvalsConfigError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExtractionEvalConfig(_Strict):
    dataset: Path
    resultados: Path
    tolerancia_pct: Decimal = Field(ge=0)
    umbral_campos_pct: Decimal = Field(ge=0, le=100)
    umbral_cabecera_pct: Decimal = Field(ge=0, le=100)
    empresas_minimas: int = Field(gt=0)
    trimestres_por_empresa: int = Field(gt=0)


class EvalsConfig(_Strict):
    version: Literal[1]
    extraccion: ExtractionEvalConfig


def load_evals_config(config_dir: Path) -> EvalsConfig:
    path = config_dir / EVALS_FILE
    try:
        return EvalsConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise EvalsConfigError(f"{path}: {exc}") from exc


# --- Dataset ------------------------------------------------------------------------------


class ExpectedFigure(_Strict):
    valor: Decimal  # en unidades de moneda (o por acción), con signo
    pagina: int = Field(gt=0)
    impreso: str  # cómo figura en el PDF (sin signo): ayuda para verificar a mano


class ExtractionCase(_Strict):
    empresa: str
    fecha_cierre: date
    tipo_balance: Literal["consolidado", "individual"]
    documento_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    moneda: str
    unidad: DocumentUnit
    base_medicion: Literal["homogenea", "nominal"]
    # Quién lo verificó contra el PDF y cuándo ("Nombre, AAAA-MM-DD"). Vacío: pendiente.
    verificado_por: str | None = None
    cifras: dict[str, ExpectedFigure] = Field(min_length=1)

    @property
    def key(self) -> tuple[str, date, str]:
        return (self.empresa, self.fecha_cierre, self.tipo_balance)


class ExtractionDataset(_Strict):
    version: Literal[1]
    casos: list[ExtractionCase]


def load_dataset(path: Path) -> ExtractionDataset:
    try:
        return ExtractionDataset.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise EvalsConfigError(f"{path}: {exc}") from exc


@dataclass(frozen=True)
class Located:
    unidad: DocumentUnit | None  # None: ganancia por acción (no se escala)
    pagina: int
    impreso: str


def locate(
    fact: CnvFact, cnv_unit: Decimal, per_share: bool, pages: Sequence[str]
) -> Located | None:
    """Dónde y en qué unidad está impresa una cifra de la CNV en el PDF."""
    if per_share:
        forms = printed_forms(fact.valor, 2)
        found = pages_with(forms, pages)
        return Located(None, found[0], forms[0]) if found else None
    # Primero la unidad de la CNV (el caso más común), después las otras.
    units = sorted(DOCUMENT_UNITS.items(), key=lambda item: item[1] != cnv_unit)
    for name, multiplier in units:
        scaled = fact.valor / multiplier
        exponent = scaled.normalize().as_tuple().exponent
        decimals = -exponent if isinstance(exponent, int) and exponent < 0 else 0
        if multiplier != cnv_unit:
            decimals = 0  # en otra unidad, el PDF redondea
            if len(str(abs(scaled.quantize(Decimal(1))))) < MIN_SCALED_DIGITS:
                continue
        forms = printed_forms(scaled, decimals)
        found = pages_with(forms, pages)
        if found:
            return Located(cast(DocumentUnit, name), found[0], forms[0])
    return None


def expected_figures(
    facts: Sequence[CnvFact], pages: Sequence[str], cnv_unit: Decimal, catalog: MetricCatalog
) -> tuple[DocumentUnit | None, dict[str, ExpectedFigure]]:
    """Precarga de un caso: las cifras de la CNV que están impresas en el PDF.

    Coinciden dos fuentes independientes, pero el caso queda pendiente hasta que una persona
    lo verifique contra el PDF. Las métricas que suman varias cuentas no se precargan: su total
    no suele estar impreso.
    """
    located: dict[str, tuple[CnvFact, Located]] = {}
    for fact in facts:
        if len(fact.montos_originales) != 1:
            continue
        per_share = catalog.metricas[fact.metrica].unidad == "moneda_por_accion"
        where = locate(fact, cnv_unit, per_share, pages)
        if where is not None:
            located[fact.metrica] = (fact, where)
    units = Counter(w.unidad for _, w in located.values() if w.unidad is not None)
    if not units:
        return None, {}
    unit = units.most_common(1)[0][0]
    figures = {
        metric: ExpectedFigure(valor=fact.valor, pagina=where.pagina, impreso=where.impreso)
        for metric, (fact, where) in located.items()
        if where.unidad in (None, unit)
    }
    return unit, figures


def merge_cases(
    existing: Sequence[ExtractionCase], prepared: Sequence[ExtractionCase]
) -> list[ExtractionCase]:
    """Agrega los casos nuevos sin pisar nunca uno verificado a mano."""
    merged = {case.key: case for case in existing}
    for case in prepared:
        current = merged.get(case.key)
        if current is None or current.verificado_por is None:
            merged[case.key] = case
    return sorted(merged.values(), key=lambda case: case.key)


# --- Puntaje ------------------------------------------------------------------------------


@dataclass
class CaseResult:
    empresa: str
    fecha_cierre: date
    verificado: bool
    campos: int
    correctos: int = 0
    cabecera_ok: bool = False
    fallas: list[str] = field(default_factory=list)
    costo_usd: Decimal = Decimal(0)
    latencia_s: float = 0.0
    error: str | None = None


def score_case(
    case: ExtractionCase,
    extraction: FinancialStatementExtraction,
    catalog: MetricCatalog,
    tolerance_pct: Decimal,
) -> CaseResult:
    result = CaseResult(
        case.empresa, case.fecha_cierre, case.verificado_por is not None, len(case.cifras)
    )
    header = {
        "moneda": (extraction.moneda, case.moneda),
        "unidad": (extraction.unidad, case.unidad),
        "base_medicion": (extraction.base_medicion, case.base_medicion),
    }
    for name, (reported, expected) in header.items():
        if reported != expected:
            result.fallas.append(f"{name}: {reported} (esperado {expected})")
    result.cabecera_ok = not result.fallas
    unit = DOCUMENT_UNITS[extraction.unidad]
    current: dict[str, Decimal] = {}
    for item in extraction.metricas:
        if item.es_comparativo or item.periodo_fin != case.fecha_cierre:
            continue
        if item.metrica not in catalog.metricas:
            continue
        scale = unit if catalog.metricas[item.metrica].unidad == "moneda" else Decimal(1)
        current.setdefault(item.metrica, item.valor * scale)
    for metric, figure in case.cifras.items():
        got = current.get(metric)
        if got is None:
            result.fallas.append(f"{metric}: falta (esperado {figure.valor})")
        elif abs(got - figure.valor) > abs(figure.valor) * tolerance_pct / 100:
            result.fallas.append(f"{metric}: {got} (esperado {figure.valor})")
        else:
            result.correctos += 1
    return result


def failed_case(case: ExtractionCase, error: str) -> CaseResult:
    """Una extracción que no respondió cuenta con todas sus cifras mal."""
    return CaseResult(
        case.empresa,
        case.fecha_cierre,
        case.verificado_por is not None,
        len(case.cifras),
        error=error,
    )


@dataclass(frozen=True)
class ModelSummary:
    modelo: str
    casos: int
    verificados: int
    empresas: int
    campos: int
    correctos: int
    cabeceras_ok: int
    errores: int
    costo_promedio_usd: Decimal
    latencia_promedio_s: float

    @property
    def exactitud_pct(self) -> Decimal:
        return Decimal(100 * self.correctos) / self.campos if self.campos else Decimal(0)

    @property
    def cabecera_pct(self) -> Decimal:
        return Decimal(100 * self.cabeceras_ok) / self.casos if self.casos else Decimal(0)

    def meets_thresholds(self, config: ExtractionEvalConfig) -> bool:
        return (
            self.casos > 0
            and self.exactitud_pct >= config.umbral_campos_pct
            and self.cabecera_pct >= config.umbral_cabecera_pct
        )

    def gate(self, config: ExtractionEvalConfig) -> Gate:
        """El GATE se evalúa solo con el dataset completo y todo verificado a mano."""
        complete = (
            self.empresas >= config.empresas_minimas
            and self.casos >= config.empresas_minimas * config.trimestres_por_empresa
            and self.verificados == self.casos
        )
        if not complete:
            return "no_evaluable"
        return "cumple" if self.meets_thresholds(config) else "no_cumple"


def summarize(model: str, results: Sequence[CaseResult]) -> ModelSummary:
    count = len(results)
    return ModelSummary(
        modelo=model,
        casos=count,
        verificados=sum(r.verificado for r in results),
        empresas=len({r.empresa for r in results}),
        campos=sum(r.campos for r in results),
        correctos=sum(r.correctos for r in results),
        cabeceras_ok=sum(r.cabecera_ok for r in results),
        errores=sum(r.error is not None for r in results),
        costo_promedio_usd=sum((r.costo_usd for r in results), Decimal(0)) / count
        if count
        else Decimal(0),
        latencia_promedio_s=sum(r.latencia_s for r in results) / count if count else 0.0,
    )


def cheapest_passing(
    summaries: Sequence[ModelSummary], config: ExtractionEvalConfig
) -> ModelSummary | None:
    """El modelo más barato que cumple los umbrales (plan técnico, sección 9)."""
    passing = [s for s in summaries if s.meets_thresholds(config)]
    return min(passing, key=lambda s: s.costo_promedio_usd, default=None)


def comparison_table(summaries: Sequence[ModelSummary], config: ExtractionEvalConfig) -> str:
    lines = [
        "| Modelo | Casos (verificados) | Cifras correctas | Cabecera correcta"
        " | Errores | Costo promedio (USD) | Latencia promedio (s) | GATE |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s.modelo} | {s.casos} ({s.verificados}) | {s.exactitud_pct:.1f} %"
            f" ({s.correctos}/{s.campos}) | {s.cabecera_pct:.1f} % | {s.errores}"
            f" | {s.costo_promedio_usd:.4f} | {s.latencia_promedio_s:.1f}"
            f" | {s.gate(config).replace('_', ' ')} |"
        )
    return "\n".join(lines)
