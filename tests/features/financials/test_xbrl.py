"""Cifras desde el XBRL de la SEC, sin LLM (T3.3)."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from brujula.features.financials.catalog import (
    FinancialsConfigError,
    MetricCatalog,
    SecXbrlMapping,
    load_metric_catalog,
    load_sec_mapping,
)
from brujula.features.financials.xbrl import extract_facts, reporting_currency

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "financials" / "companyfacts_apple.json"


@pytest.fixture
def catalog() -> MetricCatalog:
    return load_metric_catalog(ROOT / "config")


@pytest.fixture
def mapping(catalog: MetricCatalog) -> SecXbrlMapping:
    return load_sec_mapping(ROOT / "config", catalog)


def apple() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"), parse_float=Decimal)
    return data


def by_metric(facts: list[Any], metric: str) -> list[tuple[Any, ...]]:
    return [(f.periodo_inicio, f.periodo_fin, f.valor) for f in facts if f.metrica == metric]


def test_flujos_trimestrales_y_acumulados_de_apple(
    mapping: SecXbrlMapping, catalog: MetricCatalog
) -> None:
    facts = extract_facts(apple(), mapping, catalog)

    net_income = by_metric(facts, "resultado_neto")
    # Trimestre al 27/06/2026 y acumulado de 9 meses del ejercicio fiscal 2026.
    assert (date(2026, 3, 29), date(2026, 6, 27), Decimal(29789000000)) in net_income
    assert (date(2025, 9, 28), date(2026, 6, 27), Decimal(101464000000)) in net_income
    [eps] = [f for f in facts if f.metrica == "ganancia_por_accion_basica"][-1:]
    assert (eps.valor, eps.moneda, eps.unidad) == (Decimal("2.03"), "USD", "moneda_por_accion")


def test_saldos_sin_inicio_y_acciones_sin_moneda(
    mapping: SecXbrlMapping, catalog: MetricCatalog
) -> None:
    facts = extract_facts(apple(), mapping, catalog)

    assets = by_metric(facts, "activo_total")
    assert (None, date(2026, 6, 27), Decimal(383266000000)) in assets
    shares = [f for f in facts if f.metrica == "acciones_en_circulacion"]
    assert shares
    assert {(f.moneda, f.unidad) for f in shares} == {(None, "acciones")}


def test_se_usa_el_concepto_preferido_y_no_hay_periodos_repetidos(
    mapping: SecXbrlMapping, catalog: MetricCatalog
) -> None:
    facts = extract_facts(apple(), mapping, catalog)

    revenue = [f for f in facts if f.metrica == "ingresos"]
    # "Revenues" es el preferido pero solo tiene datos de 2018 (antes de `desde`).
    assert {f.concepto for f in revenue} == {
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    }
    keys = [(f.metrica, f.periodo_inicio, f.periodo_fin) for f in facts]
    assert len(keys) == len(set(keys))
    assert all(f.periodo_fin >= mapping.desde for f in facts)


def test_la_presentacion_mas_reciente_gana_y_el_concepto_preferido_tambien(
    mapping: SecXbrlMapping, catalog: MetricCatalog
) -> None:
    def row(value: int, filed: str, accn: str) -> dict[str, Any]:
        return {
            "start": "2026-01-01",
            "end": "2026-03-31",
            "val": value,
            "accn": accn,
            "fy": 2026,
            "fp": "Q1",
            "form": "10-Q",
            "filed": filed,
        }

    data = {
        "facts": {
            "us-gaap": {
                "NetIncomeLoss": {
                    "units": {
                        "USD": [row(100, "2026-05-01", "orig"), row(90, "2026-08-01", "reexp")]
                    }
                },
                "ProfitLoss": {"units": {"USD": [row(120, "2026-05-01", "pl")]}},
            }
        }
    }

    facts = extract_facts(data, mapping, catalog)

    controller = [f for f in facts if f.metrica == "resultado_neto_controladora"]
    total = [f for f in facts if f.metrica == "resultado_neto"]
    assert [(f.valor, f.accession) for f in controller] == [(Decimal(90), "reexp")]
    assert [(f.valor, f.concepto) for f in total] == [(Decimal(120), "us-gaap:ProfitLoss")]


def test_se_ignora_otra_moneda_y_formularios_no_contables(
    mapping: SecXbrlMapping, catalog: MetricCatalog
) -> None:
    def row(value: int, form: str) -> dict[str, Any]:
        return {
            "end": "2026-03-31",
            "val": value,
            "accn": "a",
            "form": form,
            "filed": "2026-05-01",
        }

    data = {
        "facts": {
            "ifrs-full": {
                "Assets": {
                    "units": {"USD": [row(100, "20-F"), row(50, "8-K")], "EUR": [row(80, "20-F")]}
                },
                "Equity": {"units": {"USD": [row(40, "20-F")]}},
            }
        }
    }

    facts = extract_facts(data, mapping, catalog)

    assert reporting_currency(data) == "USD"
    assert [(f.metrica, f.valor, f.moneda) for f in facts if f.metrica == "activo_total"] == [
        ("activo_total", Decimal(100), "USD")
    ]


def test_mapeo_con_metrica_fuera_del_catalogo(tmp_path: Path, catalog: MetricCatalog) -> None:
    (tmp_path / "sec_xbrl.yaml").write_text(
        "version: 1\ndesde: 2024-01-01\nformularios: [10-Q]\n"
        "conceptos:\n  metrica_inventada: [us-gaap:Assets]\n",
        encoding="utf-8",
    )

    with pytest.raises(FinancialsConfigError, match="metrica_inventada"):
        load_sec_mapping(tmp_path, catalog)


def test_concepto_mal_escrito(tmp_path: Path, catalog: MetricCatalog) -> None:
    (tmp_path / "sec_xbrl.yaml").write_text(
        "version: 1\ndesde: 2024-01-01\nformularios: [10-Q]\n"
        "conceptos:\n  activo_total: [Assets]\n",
        encoding="utf-8",
    )

    with pytest.raises(FinancialsConfigError, match="conceptos inválidos"):
        load_sec_mapping(tmp_path, catalog)
