"""Verificación triple contra la base, con un LLM simulado (T3.5)."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pymupdf
import pytest
from psycopg.types.json import Jsonb

from brujula.core.config import Settings
from brujula.core.db import create_engine, create_session_factory
from brujula.core.llm.client import LLMUnavailableError
from brujula.features.documents.storage import DiskStorage
from brujula.features.financials.catalog import load_metric_catalog, load_sector_metrics
from brujula.features.financials.cnv_mapping import load_cnv_accounts
from brujula.features.financials.llm_extractor import ExtractionOutcome, PagePayload
from brujula.features.financials.pipeline import StatementVerifier
from brujula.features.financials.schemas import FinancialStatementExtraction
from brujula.features.financials.verification import load_extraction_config
from brujula.features.universe.catalog import UNIVERSE_FILE, load_universe
from tests.features.financials.test_verification import CUENTAS, PAGES, extraction

pytestmark = pytest.mark.anyio

CONFIG = Path(__file__).resolve().parents[2] / "config"


def pdf(pages: Sequence[str]) -> bytes:
    document = pymupdf.open()  # type: ignore[no-untyped-call]
    for text in pages:
        document.new_page().insert_text((50, 72), text)
    content: bytes = document.tobytes()  # type: ignore[no-untyped-call]
    return content


@dataclass
class FakeExtractor:
    result: FinancialStatementExtraction | Exception
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def extract(self, *, pages: Sequence[PagePayload], **kwargs: Any) -> ExtractionOutcome:
        self.calls.append({"pages": [p.numero for p in pages], **kwargs})
        if isinstance(self.result, Exception):
            raise self.result
        return ExtractionOutcome(self.result, "modelo-de-prueba", Decimal("0.0421"))


@dataclass
class Scenario:
    settings: Settings
    storage: DiskStorage
    document_id: Any
    old_document_id: Any


@pytest.fixture
async def scenario(
    auth_settings: Settings, superuser: psycopg.Connection, tmp_path: Path
) -> Scenario:
    storage = DiskStorage(tmp_path)
    stored = await storage.save(pdf(PAGES), ".pdf")
    row = superuser.execute(
        "INSERT INTO companies (clave, nombre, sector, pais, tipo)"
        " VALUES ('PAMPA', 'Pampa', 'Energía eléctrica', 'AR', 'ar_equity') RETURNING id"
    ).fetchone()
    assert row is not None
    company_id = row[0]
    ids = []
    for presentation in (100, 101):  # la segunda reemplaza a la primera (como BYMA)
        doc = superuser.execute(
            "INSERT INTO documents (company_id, tipo, fuente, clave_externa, periodo, url_origen,"
            " nombre_archivo, tipo_contenido, hash_sha256, tamano_bytes, ruta_almacenada)"
            " VALUES (%s, 'estado_contable', 'cnv', %s, '2026-06-30', 'https://aif', 'eeff.pdf',"
            " 'application/pdf', %s, %s, %s) RETURNING id",
            (company_id, f"cnv:{presentation}:x", stored.sha256, stored.size, stored.path),
        ).fetchone()
        assert doc is not None
        ids.append(doc[0])
        superuser.execute(
            "INSERT INTO cnv_statements (presentacion_id, company_id, fecha_cierre, periodicidad,"
            " tipo_balance, moneda, unidad, norma_contable, cuentas, url_origen,"
            " fecha_publicacion, document_id) VALUES (%s, %s, '2026-06-30', 'trimestral',"
            " 'consolidado', '7', 'Miles de $', 'NIIF', %s, 'https://aif', '2026-08-06', %s)",
            (presentation, company_id, Jsonb(CUENTAS), doc[0]),
        )
    return Scenario(auth_settings, storage, ids[1], ids[0])


@pytest.fixture
async def verifier_factory(scenario: Scenario) -> AsyncIterator[Any]:
    engine = create_engine(scenario.settings)
    catalog = load_metric_catalog(CONFIG)
    universe = load_universe(CONFIG / UNIVERSE_FILE)

    def build(extractor: FakeExtractor) -> StatementVerifier:
        return StatementVerifier(
            create_session_factory(engine),
            scenario.storage,
            extractor,
            universe=universe,
            catalog=catalog,
            accounts=load_cnv_accounts(CONFIG, catalog),
            sectors=load_sector_metrics(CONFIG, catalog, {c.sector for c in universe.empresas}),
            config=load_extraction_config(CONFIG),
        )

    yield build
    await engine.dispose()


def state(conn: psycopg.Connection, document_id: Any) -> tuple[Any, ...] | None:
    return conn.execute(
        "SELECT estado, costo_verificacion_usd, verificado_en IS NOT NULL FROM documents"
        " WHERE id = %s",
        (document_id,),
    ).fetchone()


async def test_si_todo_coincide_publica_las_cifras(
    scenario: Scenario, verifier_factory: Any, superuser: psycopg.Connection
) -> None:
    extractor = FakeExtractor(extraction())

    [report] = await verifier_factory(extractor).run()

    assert (report.estado, report.diferencias) == ("validado", [])
    assert state(superuser, scenario.document_id) == ("validado", Decimal("0.042100"), True)
    # Solo la última presentación del período se verifica.
    assert state(superuser, scenario.old_document_id)[0] == "descargado"  # type: ignore[index]
    [call] = extractor.calls
    assert call["pages"] == [2, 3]  # las de los estados, no la portada
    assert call["inicio_ejercicio"] == date(2026, 1, 1)
    assert sorted(call["metrics"]) == sorted(
        [
            "activo_total",
            "pasivo_total",
            "patrimonio_neto",
            "ingresos",
            "resultado_operativo",
            "resultado_neto",
        ]
    )
    facts = superuser.execute(
        "SELECT metrica, valor, moneda, base_medicion, fecha_reexpresion, pagina, fuente,"
        " referencia, periodo_inicio FROM financial_facts ORDER BY metrica"
    ).fetchall()
    assert len(facts) == 6
    net = next(f for f in facts if f[0] == "resultado_neto")
    assert net[1:7] == (
        Decimal("-12345000.000000"),
        "ARS",
        "homogenea",
        date(2026, 6, 30),
        3,
        "cnv",
    )
    assert net[7] == "presentación 101, cuenta 3049999"
    assert net[8] == date(2026, 1, 1)


async def test_una_diferencia_deja_el_documento_en_revision_sin_publicar_nada(
    scenario: Scenario, verifier_factory: Any, superuser: psycopg.Connection
) -> None:
    extractor = FakeExtractor(extraction({"activo_total": ("1.320.000", 2)}))

    [report] = await verifier_factory(extractor).run()

    assert report.estado == "revision_manual"
    assert state(superuser, scenario.document_id)[0] == "revision_manual"  # type: ignore[index]
    assert superuser.execute("SELECT count(*) FROM financial_facts").fetchone() == (0,)
    issues = superuser.execute(
        "SELECT motivo, metrica, valor_cnv, valor_llm, pagina, modelo FROM verification_issues"
    ).fetchall()
    assert issues == [
        (
            "difiere_llm",
            "activo_total",
            Decimal("1500000000.000000"),
            Decimal("1320000000.000000"),
            2,
            "modelo-de-prueba",
        )
    ]
    # Ya no está pendiente: no se vuelve a gastar en el LLM hasta que alguien lo revise.
    assert await verifier_factory(FakeExtractor(extraction())).run() == []


async def test_si_el_llm_falla_queda_pendiente_para_reintentar(
    scenario: Scenario, verifier_factory: Any, superuser: psycopg.Connection
) -> None:
    extractor = FakeExtractor(LLMUnavailableError("modelo: Timeout"))

    [report] = await verifier_factory(extractor).run()

    assert report.error == "LLMUnavailableError: modelo: Timeout"
    assert state(superuser, scenario.document_id) == ("descargado", None, False)
    assert superuser.execute("SELECT count(*) FROM verification_issues").fetchone() == (0,)
