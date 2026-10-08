"""Verificación triple de las cifras de un estado contable argentino (T3.5, ADR 017).

Una cifra se publica solo si coinciden las tres fuentes:
1. los datos estructurados de la CNV (`cnv_mapping`);
2. el texto del PDF firmado: el número tiene que estar impreso tal cual (en la unidad de la
   CNV o reescalado a la del PDF), y se guarda su página;
3. una extracción independiente del LLM, que tampoco se cree por sí sola: el número que
   transcribe tiene que estar impreso en la página que cita.

Además, el estado tiene que pasar las validaciones contables. Cada diferencia es un `Issue`.
Si es bloqueante (una métrica obligatoria o un control global), el documento queda en
revisión manual y no se publica ninguna cifra; si no, solo esa métrica queda sin publicar.
Funciones puras, sin E/S.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from brujula.features.financials.catalog import FinancialsConfigError, MetricCatalog
from brujula.features.financials.cnv_mapping import CnvFact, parse_amount
from brujula.features.financials.schemas import FinancialStatementExtraction

EXTRACTION_FILE = "extraction.yaml"
DOCUMENT_UNITS = {"unidades": Decimal(1), "miles": Decimal(1000), "millones": Decimal(10**6)}
# Un número reescalado de pocos dígitos aparece por azar en cualquier página: no prueba nada.
MIN_SCALED_DIGITS = 4
MIN_PAGE_HINT = 7  # "1.234.5": un número con al menos 5 dígitos
MINUS = chr(0x2212)  # signo menos tipográfico, frecuente en PDFs
SIGNS = f"()-{MINUS} "


class OcrConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idioma: str = Field(min_length=3)
    dpi: int = Field(ge=72, le=600)
    max_paginas: int = Field(gt=0)


class ExtractionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    prompt: str
    tolerancia_relativa_pct: Decimal = Field(ge=0)
    tolerancia_identidad_pct: Decimal = Field(ge=0)
    factor_salto_maximo: Decimal = Field(gt=1)
    max_paginas_llm: int = Field(gt=0)
    titulos_de_estados: list[str] = Field(min_length=1)
    min_caracteres_texto: int = Field(ge=0)
    ocr: OcrConfig
    monedas_cnv: dict[str, str]


def load_extraction_config(config_dir: Path) -> ExtractionConfig:
    path = config_dir / EXTRACTION_FILE
    try:
        return ExtractionConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except OSError as exc:
        raise FinancialsConfigError(f"no se pudo leer {path}: {exc.strerror}") from None
    except (yaml.YAMLError, ValidationError) as exc:
        raise FinancialsConfigError(f"{path} no es válido:\n{exc}") from None


@dataclass(frozen=True)
class Issue:
    motivo: str
    metrica: str | None = None
    detalle: str = ""
    valor_cnv: Decimal | None = None
    valor_llm: Decimal | None = None
    pagina: int | None = None
    # Bloqueante: el documento va a revisión manual. No bloqueante: esa métrica no se publica,
    # pero el resto del estado sí (queda registrado para revisarlo).
    bloqueante: bool = True


@dataclass(frozen=True)
class VerifiedFact:
    fact: CnvFact
    pagina: int


@dataclass
class VerificationResult:
    verified: list[VerifiedFact] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.bloqueante for issue in self.issues)


# --- Números impresos --------------------------------------------------------------------


def _group(digits: str, separator: str) -> str:
    head = len(digits) % 3 or 3
    parts = [digits[:head], *(digits[i : i + 3] for i in range(head, len(digits), 3))]
    return separator.join(parts)


def printed_forms(value: Decimal, decimals: int) -> list[str]:
    """Formas en que se imprime un número (sin signo): argentina e inglesa."""
    quantum = Decimal(1).scaleb(-decimals)
    rounded = abs(value).quantize(quantum, ROUND_HALF_UP)
    integer, _, fraction = f"{rounded:f}".partition(".")
    forms = [_group(integer, "."), _group(integer, ",")]
    if fraction and int(fraction):
        forms = [f"{forms[0]},{fraction}", f"{forms[1]}.{fraction}"]
    return list(dict.fromkeys(forms))


def _pattern(printed: str) -> re.Pattern[str]:
    # No puede ser parte de un número más largo; acepta decimales en cero después (",00").
    return re.compile(rf"(?<![\d.,]){re.escape(printed)}(?:[.,]0+)?(?![\d]|[.,]\d)")


def pages_with(forms: Sequence[str], pages: Sequence[str]) -> list[int]:
    patterns = [_pattern(form) for form in forms]
    return [
        number
        for number, text in enumerate(pages, start=1)
        if any(pattern.search(text) for pattern in patterns)
    ]


def literal_forms(raw_amount: str, cnv_multiplier: Decimal, per_share: bool) -> list[str]:
    """Cómo puede aparecer en el PDF un monto de la CNV: tal cual o en otra unidad."""
    value = parse_amount(raw_amount)
    if per_share:
        return printed_forms(value, 2)
    exact = value.as_tuple().exponent
    decimals = max(0, -exact) if isinstance(exact, int) else 0
    forms = (
        printed_forms(value, decimals) if value != value.to_integral() else printed_forms(value, 0)
    )
    units = value * cnv_multiplier
    for pdf_unit in DOCUMENT_UNITS.values():
        if pdf_unit == cnv_multiplier:
            continue
        scaled = (units / pdf_unit).quantize(Decimal(1), ROUND_HALF_UP)
        if len(str(abs(scaled))) >= MIN_SCALED_DIGITS:
            forms.extend(printed_forms(scaled, 0))
    return list(dict.fromkeys(forms))


def parse_printed(text: str) -> Decimal | None:
    """Número impreso ("(1.234.567)", "1,234.5", "-12,30") con signo, o None."""
    raw = text.strip().replace(" ", "")
    negative = raw.startswith(("(", "-", MINUS)) or raw.endswith(")")
    digits = raw.strip(f"()-{MINUS}$")
    if not re.fullmatch(r"\d[\d.,]*", digits):
        return None
    if "," in digits and "." in digits:
        decimal_mark = "," if digits.rfind(",") > digits.rfind(".") else "."
    elif re.fullmatch(r"\d{1,3}([.,]\d{3})+", digits):
        decimal_mark = None  # solo separadores de miles
    else:
        decimal_mark = "," if "," in digits else "."
    if decimal_mark is None:
        normalized = digits.replace(".", "").replace(",", "")
    else:
        thousands = "." if decimal_mark == "," else ","
        normalized = digits.replace(thousands, "").replace(decimal_mark, ".")
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return None
    return -value if negative else value


# --- Verificación -------------------------------------------------------------------------


@dataclass(frozen=True)
class StatementContext:
    fecha_cierre: date
    unidad_cnv: Decimal
    moneda_cnv: str | None
    required: Sequence[str]
    # Saldos verificados del período anterior: métrica -> valor (para detectar saltos).
    previous: Mapping[str, Decimal]


def _close(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) <= tolerance


def verify(
    facts: Sequence[CnvFact],
    mapping_errors: Sequence[tuple[str, str]],
    pages: Sequence[str],
    extraction: FinancialStatementExtraction,
    context: StatementContext,
    catalog: MetricCatalog,
    config: ExtractionConfig,
) -> VerificationResult:
    """Cada cifra de la CNV se publica solo si el LLM la confirma y hay prueba impresa.

    Prueba impresa: el número de la CNV aparece en el PDF, o el número que transcribió el LLM
    aparece en la página que cita (y coincide con la CNV). Una métrica obligatoria que no se
    verifica, o un control global que falla, bloquea el documento entero.
    """
    result = VerificationResult()
    issues = result.issues
    required = set(context.required)
    by_metric = {fact.metrica: fact for fact in facts}

    if context.moneda_cnv not in config.monedas_cnv:
        issues.append(Issue("moneda_desconocida", detalle=f"código {context.moneda_cnv!r}"))
    for metric, reason in mapping_errors:
        issues.append(Issue("monto_invalido", metric, reason, bloqueante=metric in required))
    for metric in context.required:
        if metric not in by_metric:
            issues.append(Issue("falta_obligatoria", metric))
    if extraction.base_medicion != "homogenea":
        issues.append(Issue("base_medicion", detalle=f"el LLM informa {extraction.base_medicion}"))
    if extraction.fecha_cierre != context.fecha_cierre:
        issues.append(Issue("fecha_cierre", detalle=f"el LLM informa {extraction.fecha_cierre}"))

    llm_values = _llm_values(extraction, pages, context, catalog, issues)
    llm_unit = DOCUMENT_UNITS[extraction.unidad]

    for fact in facts:
        blocking = fact.metrica in required
        per_share = catalog.metricas[fact.metrica].unidad == "moneda_por_accion"
        literal = _literal_pages(fact, context.unidad_cnv, per_share, pages)
        llm = llm_values.get(fact.metrica)
        if llm is None:
            reason = "llm_falta" if literal else "no_verificable"
            issues.append(Issue(reason, fact.metrica, valor_cnv=fact.valor, bloqueante=blocking))
            continue
        llm_value, llm_page = llm
        unit = Decimal("0.01") if per_share else llm_unit
        tolerance = max(unit, abs(fact.valor) * config.tolerancia_relativa_pct / 100)
        if not _close(fact.valor, llm_value, tolerance):
            issues.append(
                Issue(
                    "difiere_llm",
                    fact.metrica,
                    valor_cnv=fact.valor,
                    valor_llm=llm_value,
                    pagina=llm_page,
                    bloqueante=blocking,
                )
            )
            continue
        result.verified.append(VerifiedFact(fact=fact, pagina=literal[0] if literal else llm_page))

    issues.extend(accounting_checks(by_metric, context, config))
    return result


def _literal_pages(
    fact: CnvFact, cnv_unit: Decimal, per_share: bool, pages: Sequence[str]
) -> list[int]:
    """Páginas donde aparecen los montos de la CNV (todos, si la métrica es una suma)."""
    found: list[int] = []
    for raw in fact.montos_originales:
        pages_for_amount = pages_with(literal_forms(raw, cnv_unit, per_share), pages)
        if not pages_for_amount:
            return []
        found = found or pages_for_amount
    return found


def _llm_values(
    extraction: FinancialStatementExtraction,
    pages: Sequence[str],
    context: StatementContext,
    catalog: MetricCatalog,
    issues: list[Issue],
) -> dict[str, tuple[Decimal, int]]:
    """Cifras del período actual que el LLM transcribió y que están impresas donde dice."""
    unit = DOCUMENT_UNITS[extraction.unidad]
    values: dict[str, tuple[Decimal, int]] = {}
    for item in extraction.metricas:
        if item.es_comparativo or item.periodo_fin != context.fecha_cierre:
            continue
        if item.metrica in values or item.metrica not in catalog.metricas:
            continue
        page = pages[item.pagina - 1] if 1 <= item.pagina <= len(pages) else ""
        printed = parse_printed(item.texto_original)
        if (
            printed is None
            or printed != item.valor
            or not pages_with([item.texto_original.strip(SIGNS)], [page])
        ):
            issues.append(
                Issue(
                    "llm_no_verificable",
                    item.metrica,
                    f"'{item.texto_original}' no está en la página {item.pagina}",
                    valor_llm=item.valor,
                    pagina=item.pagina,
                    bloqueante=False,
                )
            )
            continue
        scale = unit if catalog.metricas[item.metrica].unidad == "moneda" else Decimal(1)
        values[item.metrica] = (item.valor * scale, item.pagina)
    return values


def accounting_checks(
    facts: Mapping[str, CnvFact], context: StatementContext, config: ExtractionConfig
) -> list[Issue]:
    issues = []
    values = {metric: fact.valor for metric, fact in facts.items()}
    assets = values.get("activo_total")
    if assets is not None and "pasivo_total" in values and "patrimonio_neto" in values:
        gap = assets - values["pasivo_total"] - values["patrimonio_neto"]
        if abs(gap) > abs(assets) * config.tolerancia_identidad_pct / 100:
            issues.append(
                Issue("identidad_contable", "activo_total", f"activo - pasivo - patrimonio = {gap}")
            )
    for metric, minimum in (("activo_total", Decimal(0)), ("pasivo_total", Decimal(0))):
        if metric in values and values[metric] < minimum:
            issues.append(Issue("signo", metric, f"valor {values[metric]}"))
    if "ingresos" in values and values["ingresos"] < 0:
        issues.append(Issue("signo", "ingresos", f"valor {values['ingresos']}"))
    for metric, previous in context.previous.items():
        current = values.get(metric)
        if current is None or previous == 0 or current == 0:
            continue
        ratio = abs(current / previous)
        if ratio > config.factor_salto_maximo or ratio < 1 / config.factor_salto_maximo:
            issues.append(
                Issue("salto", metric, f"{previous} → {current} (x{ratio:.2f})", valor_cnv=current)
            )
    return issues


def pages_for_llm(
    pages: Sequence[str],
    facts: Sequence[CnvFact],
    context: StatementContext,
    catalog: MetricCatalog,
    config: ExtractionConfig,
) -> list[int]:
    """Páginas a enviar al LLM (1-based): las de los estados y las que tienen cifras de la CNV."""
    titles = [_plain(title) for title in config.titulos_de_estados]
    selected = [
        number
        for number, text in enumerate(pages, start=1)
        if any(t in _plain(text) for t in titles)
    ]
    for fact in facts:
        per_share = catalog.metricas[fact.metrica].unidad == "moneda_por_accion"
        try:
            forms = literal_forms(fact.montos_originales[0], context.unidad_cnv, per_share)
        except ValueError:
            continue
        # Un número corto aparece en muchas páginas: no ayuda a elegir cuáles mandar.
        selected.extend(pages_with([f for f in forms if len(f) >= MIN_PAGE_HINT], pages))
    ordered = sorted(set(selected)) or list(range(1, len(pages) + 1))
    return ordered[: config.max_paginas_llm]


def _plain(text: str) -> str:
    import unicodedata

    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain).upper()
