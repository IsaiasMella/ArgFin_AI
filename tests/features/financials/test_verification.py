"""Verificación triple: CNV, texto del PDF y LLM, más validaciones contables (T3.5)."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from brujula.features.financials.catalog import MetricCatalog, load_metric_catalog
from brujula.features.financials.cnv_mapping import (
    CnvAccountMap,
    CnvMapping,
    load_cnv_accounts,
    map_statement,
)
from brujula.features.financials.schemas import FinancialStatementExtraction
from brujula.features.financials.verification import (
    ExtractionConfig,
    StatementContext,
    load_extraction_config,
    pages_for_llm,
    parse_printed,
    printed_forms,
    verify,
)

CONFIG = Path(__file__).resolve().parents[3] / "config"
CIERRE = date(2026, 6, 30)

# Estado de una empresa industrial en miles de $ (como lo carga en la CNV).
CUENTAS = [
    {"nro": "1999999", "rubro": "TOTAL DEL ACTIVO", "monto": "1500000.00"},
    {"nro": "2399999", "rubro": "TOTAL DEL PASIVO", "monto": "600000.00"},
    {"nro": "2299999", "rubro": "TOTAL PATRIMONIO NETO", "monto": "900000.00"},
    {"nro": "3000100", "rubro": "INGRESOS DE ACTIVIDADES ORDINARIAS", "monto": "450123.00"},
    {"nro": "3019999", "rubro": "GANANCIA (PERDIDA) DE ACTIVIDADES OPERATIVAS", "monto": "80456"},
    {"nro": "3049999", "rubro": "GANANCIA (PERDIDA) DEL PERIODO / EJERCICIO", "monto": "-12345"},
]
# El PDF firmado: balance en la página 2, resultados en la 3.
PAGES = [
    "Estados contables intermedios al 30 de junio de 2026. Cifras en miles de pesos.",
    "ESTADO DE SITUACIÓN FINANCIERA\nTotal del activo 1.500.000 1.320.000\n"
    "Total del pasivo 600.000\nTotal del patrimonio 900.000",
    "ESTADO DE RESULTADOS\nIngresos 450.123\nResultado operativo 80.456\n"
    "Resultado del período (12.345) 9.999",
]
LLM_PAGES = {
    "activo_total": ("1.500.000", 2),
    "pasivo_total": ("600.000", 2),
    "patrimonio_neto": ("900.000", 2),
    "ingresos": ("450.123", 3),
    "resultado_operativo": ("80.456", 3),
    "resultado_neto": ("(12.345)", 3),
}


@pytest.fixture
def catalog() -> MetricCatalog:
    return load_metric_catalog(CONFIG)


@pytest.fixture
def accounts(catalog: MetricCatalog) -> CnvAccountMap:
    return load_cnv_accounts(CONFIG, catalog)


@pytest.fixture
def config() -> ExtractionConfig:
    return load_extraction_config(CONFIG)


def mapped(
    accounts: CnvAccountMap,
    catalog: MetricCatalog,
    cuentas: list[dict[str, str]] = CUENTAS,
    unidad: str = "Miles de $",
) -> CnvMapping:
    return map_statement(
        cuentas,
        unidad=unidad,
        fecha_cierre=CIERRE,
        cierre_ejercicio="12-31",
        accounts=accounts,
        catalog=catalog,
    )


def extraction(
    overrides: dict[str, tuple[str, int] | None] | None = None, **header: Any
) -> FinancialStatementExtraction:
    items = {**LLM_PAGES, **(overrides or {})}
    metrics = []
    for metric, entry in items.items():
        if entry is None:
            continue
        printed, page = entry
        value = parse_printed(printed)
        metrics.append(
            {
                "metrica": metric,
                "valor": str(value),
                "texto_original": printed,
                "pagina": page,
                "periodo_inicio": None
                if metric in {"activo_total", "pasivo_total", "patrimonio_neto"}
                else "2026-01-01",
                "periodo_fin": "2026-06-30",
                "es_comparativo": False,
            }
        )
    return FinancialStatementExtraction.model_validate(
        {
            "moneda": "ARS",
            "unidad": "miles",
            "base_medicion": "homogenea",
            "fecha_cierre": "2026-06-30",
            "tipo_balance": "consolidado",
            "metricas": metrics,
            **header,
        }
    )


def context(**changes: Any) -> StatementContext:
    base: dict[str, Any] = {
        "fecha_cierre": CIERRE,
        "unidad_cnv": Decimal(1000),
        "moneda_cnv": "7",
        "required": ["activo_total", "pasivo_total", "patrimonio_neto", "resultado_neto"],
        "previous": {},
    }
    return StatementContext(**(base | changes))


def run(
    accounts: CnvAccountMap,
    catalog: MetricCatalog,
    config: ExtractionConfig,
    *,
    pages: list[str] = PAGES,
    llm: FinancialStatementExtraction | None = None,
    ctx: StatementContext | None = None,
    cuentas: list[dict[str, str]] = CUENTAS,
    unidad: str = "Miles de $",
) -> Any:
    statement = mapped(accounts, catalog, cuentas, unidad)
    return verify(
        statement.facts,
        statement.errores,
        pages,
        llm or extraction(),
        ctx or context(),
        catalog,
        config,
    )


def reasons(result: Any) -> list[tuple[str, str | None, bool]]:
    return [(issue.motivo, issue.metrica, issue.bloqueante) for issue in result.issues]


def verified(result: Any) -> list[str]:
    return [v.fact.metrica for v in result.verified]


# --- Números impresos --------------------------------------------------------------------


def test_formas_impresas_argentina_e_inglesa() -> None:
    assert printed_forms(Decimal(9388201890), 0) == ["9.388.201.890", "9,388,201,890"]
    assert printed_forms(Decimal("205.02"), 2) == ["205,02", "205.02"]
    assert printed_forms(Decimal(-18354), 0) == ["18.354", "18,354"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("(12.345)", Decimal(-12345)),
        ("9.388.201.890", Decimal(9388201890)),
        ("1.181.332.842,59", Decimal("1181332842.59")),
        ("1,234,567.5", Decimal("1234567.5")),
        ("205,02", Decimal("205.02")),
        ("-18.354", Decimal(-18354)),
        ("n/d", None),
    ],
)
def test_numeros_impresos(text: str, expected: Decimal | None) -> None:
    assert parse_printed(text) == expected


# --- Verificación -------------------------------------------------------------------------


def test_todo_coincide_y_se_guarda_la_pagina_del_pdf(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    result = run(accounts, catalog, config)

    assert result.ok
    pages = {v.fact.metrica: v.pagina for v in result.verified}
    assert pages == {
        "activo_total": 2,
        "pasivo_total": 2,
        "patrimonio_neto": 2,
        "ingresos": 3,
        "resultado_operativo": 3,
        "resultado_neto": 3,
    }


def test_el_llm_difiere_de_la_cnv(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    # El LLM tomó el comparativo del año anterior como si fuera el actual.
    result = run(accounts, catalog, config, llm=extraction({"activo_total": ("1.320.000", 2)}))

    [issue] = result.issues
    assert (issue.motivo, issue.metrica, issue.bloqueante) == ("difiere_llm", "activo_total", True)
    assert not result.ok
    assert (issue.valor_cnv, issue.valor_llm) == (Decimal("1500000000.00"), Decimal(1320000000))


def test_una_cifra_opcional_distinta_no_se_publica_pero_no_bloquea(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    pages = [PAGES[0], PAGES[1], PAGES[2].replace("450.123", "450.132")]

    result = run(
        accounts, catalog, config, pages=pages, llm=extraction({"ingresos": ("450.132", 3)})
    )

    assert reasons(result) == [("difiere_llm", "ingresos", False)]
    assert result.ok
    assert "ingresos" not in verified(result)


def test_una_cifra_obligatoria_sin_prueba_impresa_bloquea(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    pages = [PAGES[0], PAGES[1].replace("1.500.000", "otro"), PAGES[2]]

    result = run(accounts, catalog, config, pages=pages, llm=extraction({"activo_total": None}))

    assert reasons(result) == [("no_verificable", "activo_total", True)]
    assert not result.ok


def test_numero_redondeado_en_el_pdf_se_confirma_con_lo_que_transcribe_el_llm(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    # La CNV tiene decimales que el PDF (en miles) no imprime: el número exacto no aparece,
    # pero el LLM transcribe el impreso, que está en su página y coincide dentro del redondeo.
    cuentas = [{**c, "monto": "450123.40"} if c["nro"] == "3000100" else c for c in CUENTAS]

    result = run(accounts, catalog, config, cuentas=cuentas)

    assert result.issues == []
    assert {v.fact.metrica: v.pagina for v in result.verified}["ingresos"] == 3


def test_el_llm_cita_una_pagina_donde_no_esta_el_numero(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    result = run(accounts, catalog, config, llm=extraction({"ingresos": ("450.123", 2)}))

    assert reasons(result) == [
        ("llm_no_verificable", "ingresos", False),
        ("llm_falta", "ingresos", False),
    ]
    assert result.ok
    assert "ingresos" not in verified(result)


def test_el_llm_omite_una_metrica(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    result = run(accounts, catalog, config, llm=extraction({"resultado_operativo": None}))

    assert reasons(result) == [("llm_falta", "resultado_operativo", False)]
    assert "resultado_operativo" not in verified(result)


def test_identidad_contable_que_no_cierra(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    cuentas = [{**c, "monto": "900410.00"} if c["nro"] == "2299999" else c for c in CUENTAS]
    pages = [PAGES[0], PAGES[1].replace("900.000", "900.410"), PAGES[2]]

    result = run(
        accounts,
        catalog,
        config,
        pages=pages,
        cuentas=cuentas,
        llm=extraction({"patrimonio_neto": ("900.410", 2)}),
    )

    assert reasons(result) == [("identidad_contable", "activo_total", True)]


def test_salto_absurdo_contra_el_periodo_anterior(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    result = run(
        accounts,
        catalog,
        config,
        ctx=context(previous={"activo_total": Decimal(100_000_000)}),
    )

    assert reasons(result) == [("salto", "activo_total", True)]


def test_falta_una_metrica_obligatoria_del_sector(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    result = run(
        accounts,
        catalog,
        config,
        ctx=context(required=["activo_total", "flujo_operativo"]),
    )

    assert reasons(result) == [("falta_obligatoria", "flujo_operativo", True)]


def test_moneda_desconocida_y_base_nominal(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    result = run(
        accounts,
        catalog,
        config,
        ctx=context(moneda_cnv="36"),
        llm=extraction(base_medicion="nominal"),
    )

    assert reasons(result) == [("moneda_desconocida", None, True), ("base_medicion", None, True)]


def test_monto_ambiguo_en_una_metrica_obligatoria(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    # "1.234" puede ser mil doscientos o uno coma dos: no se adivina (ADR 016).
    cuentas = [{**c, "monto": "1.234"} if c["nro"] == "2399999" else c for c in CUENTAS]

    result = run(accounts, catalog, config, cuentas=cuentas)

    assert ("monto_invalido", "pasivo_total", True) in reasons(result)
    assert not result.ok
    assert "pasivo_total" not in verified(result)


def test_ingresos_negativos(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    cuentas = [{**c, "monto": "-450123.00"} if c["nro"] == "3000100" else c for c in CUENTAS]

    result = run(
        accounts, catalog, config, cuentas=cuentas, llm=extraction({"ingresos": ("(450.123)", 3)})
    )

    assert reasons(result) == [("signo", "ingresos", True)]
    assert not result.ok


def test_cnv_en_pesos_y_pdf_en_miles(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    # Una empresa que carga la CNV en pesos y publica el PDF en miles, redondeado.
    cuentas = [
        {"nro": "1999999", "rubro": "TOTAL DEL ACTIVO", "monto": "3898429619145.00"},
        {"nro": "2399999", "rubro": "TOTAL DEL PASIVO", "monto": "1518866776678.00"},
        {"nro": "2299999", "rubro": "TOTAL PATRIMONIO NETO", "monto": "2379562842467.00"},
        {"nro": "3049999", "rubro": "GANANCIA (PERDIDA) DEL PERIODO / EJERCICIO", "monto": "1"},
    ]
    pages = [
        "ESTADO DE SITUACION FINANCIERA (en miles de pesos)\nActivo 3.898.429.619\n"
        "Pasivo 1.518.866.777\nPatrimonio 2.379.562.842"
    ]
    llm = extraction(
        {
            "activo_total": ("3.898.429.619", 1),
            "pasivo_total": ("1.518.866.777", 1),
            "patrimonio_neto": ("2.379.562.842", 1),
            "ingresos": None,
            "resultado_operativo": None,
            "resultado_neto": None,
        }
    )

    result = run(
        accounts,
        catalog,
        config,
        pages=pages,
        cuentas=cuentas,
        llm=llm,
        ctx=context(unidad_cnv=Decimal(1), required=[]),
        unidad="$",
    )

    assert [v.fact.metrica for v in result.verified] == [
        "activo_total",
        "pasivo_total",
        "patrimonio_neto",
    ]
    # El resultado neto de 1 peso no aparece en el PDF: no se puede verificar ni publicar.
    assert reasons(result) == [("no_verificable", "resultado_neto", False)]
    assert result.ok


def test_paginas_para_el_llm(
    accounts: CnvAccountMap, catalog: MetricCatalog, config: ExtractionConfig
) -> None:
    pages = ["Índice", *PAGES[1:], "Nota 5: otros", "Nota 9: total del activo 1.500.000"]

    selected = pages_for_llm(pages, mapped(accounts, catalog).facts, context(), catalog, config)

    assert selected == [2, 3, 5]
