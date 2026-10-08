"""Dataset de referencia, puntaje y runner de la eval de extracción (T3.6)."""

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from evals.runners.extraction import run_eval, save_results, write_dataset
from evals.runners.scoring import (
    ExpectedFigure,
    ExtractionCase,
    ExtractionEvalConfig,
    cheapest_passing,
    comparison_table,
    expected_figures,
    load_dataset,
    load_evals_config,
    merge_cases,
    score_case,
    summarize,
)

from brujula.core.llm.client import LLMUnavailableError
from brujula.features.financials.catalog import MetricCatalog, load_metric_catalog
from brujula.features.financials.cnv_mapping import CnvAccountMap, load_cnv_accounts
from brujula.features.financials.llm_extractor import ExtractionOutcome
from brujula.features.financials.schemas import FinancialStatementExtraction
from tests.features.financials.test_verification import PAGES, extraction, mapped

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64


@pytest.fixture
def catalog() -> MetricCatalog:
    return load_metric_catalog(ROOT / "config")


@pytest.fixture
def accounts(catalog: MetricCatalog) -> CnvAccountMap:
    return load_cnv_accounts(ROOT / "config", catalog)


@pytest.fixture
def config(tmp_path: Path) -> ExtractionEvalConfig:
    base = load_evals_config(ROOT / "config").extraccion
    return base.model_copy(
        update={"dataset": tmp_path / "dataset.yaml", "resultados": tmp_path / "resultados"}
    )


def case(
    empresa: str = "PAMPA", verificado_por: str | None = None, **changes: Any
) -> ExtractionCase:
    data: dict[str, Any] = {
        "empresa": empresa,
        "fecha_cierre": date(2026, 6, 30),
        "tipo_balance": "consolidado",
        "documento_sha256": SHA,
        "moneda": "ARS",
        "unidad": "miles",
        "base_medicion": "homogenea",
        "verificado_por": verificado_por,
        "cifras": {
            "activo_total": ExpectedFigure(
                valor=Decimal(1_500_000_000), pagina=2, impreso="1.500.000"
            ),
            "ingresos": ExpectedFigure(valor=Decimal(450_123_000), pagina=3, impreso="450.123"),
            "resultado_neto": ExpectedFigure(
                valor=Decimal(-12_345_000), pagina=3, impreso="12.345"
            ),
        },
    }
    return ExtractionCase(**(data | changes))


# --- Precarga del dataset -----------------------------------------------------------------


def test_precarga_las_cifras_de_la_cnv_impresas_en_el_pdf(
    accounts: CnvAccountMap, catalog: MetricCatalog
) -> None:
    statement = mapped(accounts, catalog)

    unit, figures = expected_figures(statement.facts, PAGES, Decimal(1000), catalog)

    assert unit == "miles"
    assert figures["activo_total"] == ExpectedFigure(
        valor=Decimal("1500000000.00"), pagina=2, impreso="1.500.000"
    )
    assert figures["resultado_neto"].pagina == 3
    assert set(figures) == {
        "activo_total",
        "pasivo_total",
        "patrimonio_neto",
        "ingresos",
        "resultado_operativo",
        "resultado_neto",
    }


def test_una_cifra_que_no_esta_impresa_no_se_precarga(
    accounts: CnvAccountMap, catalog: MetricCatalog
) -> None:
    pages = [PAGES[0], PAGES[1], PAGES[2].replace("80.456", "-")]

    _, figures = expected_figures(mapped(accounts, catalog).facts, pages, Decimal(1000), catalog)

    assert "resultado_operativo" not in figures


def test_cnv_en_pesos_y_pdf_en_miles(accounts: CnvAccountMap, catalog: MetricCatalog) -> None:
    # Como Aluar: la CNV en pesos con centavos, el PDF en miles redondeados.
    cuentas = [
        {"nro": "1999999", "rubro": "TOTAL DEL ACTIVO", "monto": "3898429619145.00"},
        {"nro": "3049999", "rubro": "GANANCIA (PERDIDA) DEL PERIODO / EJERCICIO", "monto": "-512"},
    ]
    pages = ["Total del activo 3.898.429.619", "Resultado (512)"]

    unit, figures = expected_figures(
        mapped(accounts, catalog, cuentas, "$").facts, pages, Decimal(1), catalog
    )

    assert unit == "miles"
    assert figures["activo_total"].impreso == "3.898.429.619"
    # 512 pesos serían "1" en miles: un número tan corto no prueba nada y no se precarga.
    assert "resultado_neto" not in figures


def test_preparar_de_nuevo_nunca_pisa_un_caso_verificado() -> None:
    verified = case("GGAL", verificado_por="Isaías, 2026-10-09")
    pending = case("YPF")
    fresh = [case("GGAL", moneda="USD"), case("YPF", moneda="USD"), case("TGS")]

    merged = merge_cases([verified, pending], fresh)

    assert [(c.empresa, c.moneda) for c in merged] == [
        ("GGAL", "ARS"),  # verificado: se conserva
        ("TGS", "ARS"),
        ("YPF", "USD"),  # pendiente: se reemplaza
    ]


def test_el_dataset_se_escribe_y_se_vuelve_a_leer(config: ExtractionEvalConfig) -> None:
    cases = [case(), case("GGAL", verificado_por="Isaías, 2026-10-09")]

    write_dataset(config.dataset, cases)

    text = config.dataset.read_text(encoding="utf-8")
    assert "PENDIENTE DE VERIFICACIÓN HUMANA" in text
    assert load_dataset(config.dataset).casos == cases


# --- Puntaje ------------------------------------------------------------------------------


def test_todo_correcto(catalog: MetricCatalog) -> None:
    result = score_case(case(), extraction(), catalog, Decimal("0.5"))

    assert (result.campos, result.correctos, result.cabecera_ok, result.fallas) == (
        3,
        3,
        True,
        [],
    )


def test_cifras_fuera_de_tolerancia_o_faltantes(catalog: MetricCatalog) -> None:
    llm = extraction({"ingresos": ("452.500", 3), "resultado_neto": None})

    result = score_case(case(), llm, catalog, Decimal("0.5"))

    assert result.correctos == 1
    assert result.fallas == [
        "ingresos: 452500000 (esperado 450123000)",
        "resultado_neto: falta (esperado -12345000)",
    ]


def test_dentro_de_la_tolerancia_es_correcta(catalog: MetricCatalog) -> None:
    llm = extraction({"ingresos": ("451.000", 3)})  # 0,19 % de diferencia

    assert score_case(case(), llm, catalog, Decimal("0.5")).correctos == 3


def test_la_unidad_equivocada_falla_la_cabecera_y_las_cifras(catalog: MetricCatalog) -> None:
    llm = extraction(unidad="millones")

    result = score_case(case(), llm, catalog, Decimal("0.5"))

    assert not result.cabecera_ok
    assert result.correctos == 0
    assert result.fallas[0] == "unidad: millones (esperado miles)"


# --- Resumen y GATE -----------------------------------------------------------------------


def results_for(catalog: MetricCatalog, companies: int, quarters: int, verified: bool) -> Any:
    out = []
    for n in range(companies):
        for q in range(quarters):
            c = case(f"E{n}", "Isaías, 2026-10-09" if verified else None)
            result = score_case(c, extraction(), catalog, Decimal("0.5"))
            result.costo_usd = Decimal("0.02") + Decimal(q) / 100
            out.append(result)
    return out


def test_gate_no_evaluable_con_casos_sin_verificar(
    catalog: MetricCatalog, config: ExtractionEvalConfig
) -> None:
    summary = summarize("a/modelo", results_for(catalog, 10, 4, verified=False))

    assert summary.meets_thresholds(config)
    assert summary.gate(config) == "no_evaluable"


def test_gate_no_evaluable_con_el_dataset_incompleto(
    catalog: MetricCatalog, config: ExtractionEvalConfig
) -> None:
    summary = summarize("a/modelo", results_for(catalog, 9, 4, verified=True))

    assert summary.gate(config) == "no_evaluable"


def test_gate_cumple_y_no_cumple(catalog: MetricCatalog, config: ExtractionEvalConfig) -> None:
    good = results_for(catalog, 10, 4, verified=True)
    bad = results_for(catalog, 10, 4, verified=True)
    for result in bad[:3]:
        result.correctos = 0  # 9 de 120 cifras mal: 92,5 %

    assert summarize("a/bueno", good).gate(config) == "cumple"
    assert summarize("b/malo", bad).exactitud_pct == Decimal("92.5")
    assert summarize("b/malo", bad).gate(config) == "no_cumple"


def test_el_mas_barato_que_cumple(catalog: MetricCatalog, config: ExtractionEvalConfig) -> None:
    results = results_for(catalog, 10, 4, verified=True)
    expensive = summarize("caro/modelo", results)
    cheap = replace(summarize("barato/modelo", results), costo_promedio_usd=Decimal("0.001"))
    failing = replace(summarize("barato/malo", results), costo_promedio_usd=Decimal(0), correctos=0)

    assert cheapest_passing([expensive, cheap, failing], config) == cheap
    assert cheapest_passing([failing], config) is None
    table = comparison_table([expensive, failing], config)
    assert "| caro/modelo | 40 (40) | 100.0 % (120/120) | 100.0 % | 0 |" in table
    assert table.endswith("| no cumple |")


# --- Runner -------------------------------------------------------------------------------


@dataclass
class FakeVerifier:
    """Mismo contrato que StatementVerifier para `latest`, `prepare` y `extract`."""

    result: FinancialStatementExtraction | Exception
    sha: str = SHA
    prepared: int = 0
    calls: list[str] = field(default_factory=list)

    async def latest(self, *, only: str | None = None) -> list[Any]:
        document = type("Doc", (), {"hash_sha256": self.sha})()
        return [type("Item", (), {"document": document, "clave": only})()]

    async def prepare(self, item: Any) -> str:
        self.prepared += 1
        return "preparado"

    async def extract(self, item: Any, prepared: Any) -> ExtractionOutcome:
        self.calls.append(item.clave)
        if isinstance(self.result, Exception):
            raise self.result
        return ExtractionOutcome(self.result, "proveedor/modelo", Decimal("0.03"))


@pytest.mark.anyio
async def test_compara_modelos_preparando_cada_documento_una_vez(
    catalog: MetricCatalog, config: ExtractionEvalConfig
) -> None:
    good = FakeVerifier(extraction())
    down = FakeVerifier(LLMUnavailableError("b/modelo: Timeout"))
    cases = [case("PAMPA"), case("GGAL"), case("YPF", documento_sha256="b" * 64)]

    results = await run_eval(
        cases,
        ["a/modelo", "b/modelo"],
        {"a/modelo": good, "b/modelo": down},  # type: ignore[dict-item]
        good,  # type: ignore[arg-type]
        catalog=catalog,
        tolerance=Decimal("0.5"),
    )

    assert good.prepared == 2  # YPF: el documento ya no es el del dataset
    assert [(r.empresa, r.correctos, r.error) for r in results["a/modelo"]] == [
        ("PAMPA", 3, None),
        ("GGAL", 3, None),
        ("YPF", 0, "documento no encontrado"),
    ]
    assert [r.error for r in results["b/modelo"]] == [
        "LLMUnavailableError: b/modelo: Timeout",
        "LLMUnavailableError: b/modelo: Timeout",
        "documento no encontrado",
    ]
    assert results["a/modelo"][0].costo_usd == Decimal("0.03")

    config.dataset.write_text("version: 1\ncasos: []\n", encoding="utf-8")
    summaries = [summarize(m, results[m]) for m in results]
    paths = save_results(
        config,
        config.dataset,
        "extraer_estado_contable@1",
        summaries,
        results,
        datetime(2026, 10, 8, 12, 30, tzinfo=UTC),
    )

    assert [p.name for p in paths] == [
        "2026-10-08T1230_a-modelo_extraer_estado_contable@1.json",
        "2026-10-08T1230_b-modelo_extraer_estado_contable@1.json",
        "2026-10-08T1230_comparacion.md",
    ]
    assert '"gate": "no_evaluable"' in paths[0].read_text(encoding="utf-8")
