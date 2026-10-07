"""Plan de cuentas de la CNV a métricas internas y esquema de extracción (T3.4)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from brujula.features.documents.sources.cnv import parse_presentation
from brujula.features.financials.catalog import (
    FinancialsConfigError,
    MetricCatalog,
    load_metric_catalog,
    load_sector_metrics,
)
from brujula.features.financials.cnv_mapping import (
    AmountError,
    CnvAccountMap,
    EmptyAmountError,
    fiscal_year_start,
    load_cnv_accounts,
    map_statement,
    normalize_label,
    parse_amount,
    unit_multiplier,
)
from brujula.features.financials.schemas import FinancialStatementExtraction
from brujula.features.universe.catalog import UNIVERSE_FILE, load_universe

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config"
GGAL_PRESENTATION = ROOT / "tests" / "fixtures" / "documents" / "aif_estado_contable.html"


@pytest.fixture
def catalog() -> MetricCatalog:
    return load_metric_catalog(CONFIG)


@pytest.fixture
def accounts(catalog: MetricCatalog) -> CnvAccountMap:
    return load_cnv_accounts(CONFIG, catalog)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("329308168.00", Decimal("329308168.00")),
        ("25756262378", Decimal(25756262378)),
        (" -4836707.00", Decimal("-4836707.00")),
        ("43.346.287", Decimal(43346287)),
        ("1.3", Decimal("1.3")),
        ("0.01", Decimal("0.01")),
    ],
)
def test_montos_de_la_cnv(text: str, expected: Decimal) -> None:
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["1.234", "12,5", "abc", "1.2.3"])
def test_montos_ambiguos_o_invalidos_no_se_adivinan(text: str) -> None:
    with pytest.raises(AmountError):
        parse_amount(text)


@pytest.mark.parametrize("text", ["-", "", " "])
def test_cuenta_sin_saldo(text: str) -> None:
    with pytest.raises(EmptyAmountError):
        parse_amount(text)


def test_unidades() -> None:
    assert unit_multiplier("Miles de $") == Decimal(1000)
    assert unit_multiplier("Millones  de $") == Decimal(10**6)
    assert unit_multiplier("$") == Decimal(1)
    with pytest.raises(AmountError, match="unidad desconocida"):
        unit_multiplier("Miles de US$")


@pytest.mark.parametrize(
    ("period_end", "fiscal_end", "expected"),
    [
        (date(2026, 6, 30), "12-31", date(2026, 1, 1)),
        (date(2025, 12, 31), "12-31", date(2025, 1, 1)),
        (date(2026, 3, 31), "06-30", date(2025, 7, 1)),
        (date(2026, 6, 30), "06-30", date(2025, 7, 1)),
    ],
)
def test_inicio_del_ejercicio(period_end: date, fiscal_end: str, expected: date) -> None:
    assert fiscal_year_start(period_end, fiscal_end) == expected


def test_rubros_normalizados() -> None:
    assert normalize_label("Diferencia de cotización  de oro") == "DIFERENCIA DE COTIZACION DE ORO"


def test_estado_real_de_ggal_en_miles(accounts: CnvAccountMap, catalog: MetricCatalog) -> None:
    presentation = parse_presentation(GGAL_PRESENTATION.read_text(encoding="utf-8"))

    mapped = map_statement(
        presentation.cuentas,
        unidad="Miles de $",
        fecha_cierre=date(2026, 6, 30),
        cierre_ejercicio="12-31",
        accounts=accounts,
        catalog=catalog,
    )

    facts = {f.metrica: f for f in mapped.facts}
    assert mapped.errores == []
    assert facts["activo_total"].valor == Decimal("9388201890000.00")
    assert facts["activo_total"].periodo_inicio is None
    net = facts["resultado_neto"]
    assert (net.valor, net.periodo_inicio, net.montos_originales) == (
        Decimal("329308168000.00"),
        date(2026, 1, 1),
        ("329308168.00",),
    )
    # La ganancia por acción no se multiplica por la unidad.
    assert facts["ganancia_por_accion_basica"].valor == Decimal("205.02")
    # Plantilla de bancos: 3000100 son ingresos por intereses, no "ingresos".
    assert facts["ingresos_por_intereses"].valor == Decimal(23160000)
    assert "ingresos" not in facts
    total = facts["pasivo_total"].valor + facts["patrimonio_neto"].valor
    assert facts["activo_total"].valor == total


def test_plantilla_de_industria_suma_deuda_y_no_confunde_codigos(
    accounts: CnvAccountMap, catalog: MetricCatalog
) -> None:
    cuentas = [
        {"nro": "3000100", "rubro": "INGRESOS DE ACTIVIDADES ORDINARIAS", "monto": "1000"},
        {"nro": "2322200", "rubro": "PASIVOS FINANCIEROS CORRIENTES", "monto": "30"},
        {"nro": "2312300", "rubro": "PASIVOS FINANCIEROS NO CORRIENTES", "monto": "70"},
        {"nro": "3241100", "rubro": "TOTAL CAMBIOS EN ACTIVOS Y PASIVOS OPERATIVOS", "monto": "5"},
        {"nro": "3241200", "rubro": "TOTAL DE ACTIVIDADES DE INVERSION", "monto": "-8"},
        {"nro": "3011600", "rubro": "DEPRECIACIONES Y AMORTIZACIONES", "monto": "-"},
        {"nro": "8000004", "rubro": "EBITDA", "monto": "1.234"},
    ]

    mapped = map_statement(
        cuentas,
        unidad="$",
        fecha_cierre=date(2026, 3, 31),
        cierre_ejercicio="06-30",
        accounts=accounts,
        catalog=catalog,
    )

    facts = {f.metrica: f for f in mapped.facts}
    assert facts["ingresos"].valor == Decimal(1000)
    assert facts["ingresos"].periodo_inicio == date(2025, 7, 1)
    assert (facts["deuda_financiera"].valor, facts["deuda_financiera"].cuentas) == (
        Decimal(100),
        ("2322200", "2312300"),
    )
    assert facts["flujo_inversion"].valor == Decimal(-8)
    assert "flujo_operativo" not in facts  # 3241100 de industria no es el flujo operativo
    assert "depreciaciones_y_amortizaciones" not in facts  # sin saldo
    assert mapped.errores == [("ebitda", "formato ambiguo: '1.234'")]


def test_unidad_desconocida_no_mapea_nada(accounts: CnvAccountMap, catalog: MetricCatalog) -> None:
    mapped = map_statement(
        [{"nro": "1999999", "rubro": "TOTAL DEL ACTIVO", "monto": "10"}],
        unidad="Miles de US$",
        fecha_cierre=date(2026, 3, 31),
        cierre_ejercicio="12-31",
        accounts=accounts,
        catalog=catalog,
    )

    assert (mapped.facts, mapped.errores) == ([], [("*", "unidad desconocida: 'Miles de US$'")])


def test_todos_los_sectores_del_universo_tienen_metricas_obligatorias(
    catalog: MetricCatalog,
) -> None:
    universe = load_universe(CONFIG / UNIVERSE_FILE)

    sectors = load_sector_metrics(CONFIG, catalog, {c.sector for c in universe.empresas})

    assert sectors.required("Petróleo y gas") == [
        "activo_total",
        "pasivo_total",
        "patrimonio_neto",
        "resultado_neto",
        "ingresos",
        "resultado_operativo",
    ]


def test_sector_faltante(catalog: MetricCatalog) -> None:
    with pytest.raises(FinancialsConfigError, match="Minería"):
        load_sector_metrics(CONFIG, catalog, {"Minería"})


def test_regla_con_metrica_inexistente(tmp_path: Path, catalog: MetricCatalog) -> None:
    (tmp_path / "cnv_accounts.yaml").write_text(
        "version: 1\nreglas:\n  - {metrica: inventada, nro: '1', rubro: 'X'}\n", encoding="utf-8"
    )

    with pytest.raises(FinancialsConfigError, match="inventada"):
        load_cnv_accounts(tmp_path, catalog)


def test_esquema_de_extraccion_exige_pagina_y_texto_original() -> None:
    base = {
        "moneda": "ARS",
        "unidad": "miles",
        "base_medicion": "homogenea",
        "fecha_cierre": "2026-06-30",
        "tipo_balance": "consolidado",
    }
    metric = {
        "metrica": "activo_total",
        "valor": "9388201890",
        "texto_original": "9.388.201.890",
        "pagina": 3,
        "periodo_inicio": None,
        "periodo_fin": "2026-06-30",
        "es_comparativo": False,
    }

    extraction = FinancialStatementExtraction.model_validate({**base, "metricas": [metric]})

    assert extraction.metricas[0].valor == Decimal(9388201890)
    with pytest.raises(ValidationError):
        FinancialStatementExtraction.model_validate({**base, "metricas": [{**metric, "pagina": 0}]})
    with pytest.raises(ValidationError):
        FinancialStatementExtraction.model_validate({**base, "unidad": "cientos", "metricas": []})
