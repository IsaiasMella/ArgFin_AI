"""Cifras desde `companyfacts` de la SEC (XBRL), sin LLM (T3.3, ADR 015).

Para cada métrica interna se recorren sus conceptos XBRL en orden de preferencia:
- Un período (inicio, fin) toma el valor del concepto de mayor preferencia que lo tenga.
- Si el mismo concepto aparece en varias presentaciones (el dato original y luego como
  comparativo, quizá reexpresado), gana la presentación más reciente.
- Se usa una sola moneda por empresa: la de sus estados (la más frecuente en sus datos).
"""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from brujula.features.financials.catalog import Metric, MetricCatalog, SecXbrlMapping

CURRENCY = re.compile(r"^[A-Z]{3}$")
PER_SHARE = re.compile(r"^([A-Z]{3})/shares$")


@dataclass(frozen=True)
class XbrlFact:
    metrica: str
    concepto: str
    periodo_inicio: date | None
    periodo_fin: date
    valor: Decimal
    moneda: str | None
    unidad: str
    formulario: str
    accession: str
    fecha_presentacion: date


def _unit(unit_key: str, metric: Metric) -> tuple[str | None, str] | None:
    """(moneda, unidad) de un `unit` XBRL, o None si no corresponde a la métrica."""
    if metric.unidad == "moneda" and CURRENCY.match(unit_key):
        return unit_key, "moneda"
    if metric.unidad == "moneda_por_accion" and (match := PER_SHARE.match(unit_key)):
        return match.group(1), "moneda_por_accion"
    if metric.unidad == "acciones" and unit_key == "shares":
        return None, "acciones"
    return None


def reporting_currency(companyfacts: dict[str, Any]) -> str | None:
    """La moneda en que la empresa presenta sus estados: la más frecuente en sus datos."""
    counts: Counter[str] = Counter()
    for concepts in companyfacts.get("facts", {}).values():
        for concept in concepts.values():
            for unit_key, rows in concept.get("units", {}).items():
                if CURRENCY.match(unit_key):
                    counts[unit_key] += len(rows)
    return counts.most_common(1)[0][0] if counts else None


def extract_facts(
    companyfacts: dict[str, Any], mapping: SecXbrlMapping, catalog: MetricCatalog
) -> list[XbrlFact]:
    facts = companyfacts.get("facts", {})
    currency = reporting_currency(companyfacts)
    forms = set(mapping.formularios)
    result: list[XbrlFact] = []
    for metric_code, concepts in mapping.conceptos.items():
        metric = catalog.metricas[metric_code]
        # (inicio, fin) -> (preferencia, fact)
        chosen: dict[tuple[date | None, date], tuple[int, XbrlFact]] = {}
        for priority, concept in enumerate(concepts):
            taxonomy, name = concept.split(":", 1)
            units = facts.get(taxonomy, {}).get(name, {}).get("units", {})
            for unit_key, rows in units.items():
                unit = _unit(unit_key, metric)
                if unit is None or (unit[0] is not None and unit[0] != currency):
                    continue
                for row in rows:
                    fact = _fact(row, metric_code, metric, concept, unit, forms, mapping.desde)
                    if fact is None:
                        continue
                    key = (fact.periodo_inicio, fact.periodo_fin)
                    current = chosen.get(key)
                    if (
                        current is None
                        or priority < current[0]
                        or (
                            priority == current[0]
                            and fact.fecha_presentacion > current[1].fecha_presentacion
                        )
                    ):
                        chosen[key] = (priority, fact)
        result.extend(fact for _, fact in chosen.values())
    return sorted(
        result, key=lambda f: (f.metrica, f.periodo_fin, f.periodo_inicio or f.periodo_fin)
    )


def _fact(
    row: dict[str, Any],
    metric_code: str,
    metric: Metric,
    concept: str,
    unit: tuple[str | None, str],
    forms: set[str],
    since: date,
) -> XbrlFact | None:
    try:
        if row.get("form") not in forms:
            return None
        end = date.fromisoformat(row["end"])
        start = date.fromisoformat(row["start"]) if row.get("start") else None
        # Un saldo es a una fecha; un flujo necesita su período.
        if end < since or (metric.tipo == "saldo") != (start is None):
            return None
        return XbrlFact(
            metrica=metric_code,
            concepto=concept,
            periodo_inicio=start,
            periodo_fin=end,
            valor=Decimal(row["val"]),
            moneda=unit[0],
            unidad=unit[1],
            formulario=row["form"],
            accession=row["accn"],
            fecha_presentacion=date.fromisoformat(row["filed"]),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None
