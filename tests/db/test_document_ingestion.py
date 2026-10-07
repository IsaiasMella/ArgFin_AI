"""Ingesta de documentos contra la base, con fuentes simuladas (T3.2)."""

from datetime import date
from pathlib import Path
from typing import Any

import httpx2
import psycopg
import pytest

from brujula.core.config import Settings
from brujula.features.documents.ingest import CompanyReport
from brujula.features.documents.tasks import ingest_documents
from brujula.features.universe.catalog import UNIVERSE_FILE, Universe, load_universe
from tests.support.documents import (
    GGAL_CIK,
    GGAL_CUIT,
    OTHER_6K,
    RESULTS_6K,
    SITE,
    WORDPRESS,
    FakeSources,
)

pytestmark = pytest.mark.anyio

SINCE = date(2026, 1, 1)


def _company(clave: str, **extra: Any) -> dict[str, Any]:
    return {
        "clave": clave,
        "nombre": clave,
        "tipo": "ar_equity",
        "sector": "Prueba",
        "pais": "AR",
        "instrumentos": [{"ticker_byma": clave[:5], "moneda": "ARS"}],
        **extra,
    }


def _cnv(cuit: str, balance: str = "consolidado") -> dict[str, Any]:
    return {"cuit": cuit, "id": 1, "balance": balance, "cierre_ejercicio": "12-31"}


@pytest.fixture
def settings_with_universe(
    auth_settings: Settings, tmp_path: Path, superuser: psycopg.Connection
) -> Settings:
    versioned = load_universe(auth_settings.config_dir / UNIVERSE_FILE)
    universe = Universe.model_validate(
        {
            **versioned.model_dump(mode="json"),
            "empresas": [
                _company(
                    "GGAL",
                    cik_sec=GGAL_CIK,
                    cnv=_cnv(GGAL_CUIT),
                    comunicados=["cnv_hecho_relevante", "sec_6k"],
                ),
                _company(
                    "TGN",
                    cnv=_cnv("30657863056", "individual"),
                    comunicados=["sitio_inversores"],
                    inversores={
                        "url": f"{SITE}/inversores",
                        "acceso": "enlaces_pdf",
                        "patron": r"informe_de_resultados_\dq_\d{4}",
                    },
                ),
                _company(
                    "VALO",
                    cnv=_cnv("30576124275"),
                    comunicados=["sitio_inversores"],
                    inversores={
                        "url": WORDPRESS,
                        "acceso": "wordpress_media",
                        "patron": r"IR-\d{4}Q\d-ESP-Press-Release",
                    },
                ),
            ],
        }
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / UNIVERSE_FILE).write_text(universe.model_dump_json(), encoding="utf-8")
    documents_yaml = auth_settings.config_dir / "documents.yaml"
    (config_dir / "documents.yaml").write_text(
        documents_yaml.read_text(encoding="utf-8"), encoding="utf-8"
    )
    for company in universe.empresas:
        superuser.execute(
            "INSERT INTO companies (clave, nombre, sector, pais, tipo)"
            " VALUES (%s, %s, 'Prueba', 'AR', 'ar_equity')",
            (company.clave, company.nombre),
        )
    return auth_settings.model_copy(
        update={"config_dir": config_dir, "document_storage_dir": tmp_path / "docs"}
    )


async def run(
    settings: Settings, sources: FakeSources, only: str | None = None
) -> dict[str, CompanyReport]:
    reports = await ingest_documents(
        settings, since=SINCE, only=only, http_transport=sources.transport()
    )
    return {r.clave: r for r in reports}


def documents(conn: psycopg.Connection, clave: str) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT d.tipo, d.fuente, d.nombre_archivo, d.periodo, d.ruta_almacenada"
        " FROM documents d JOIN companies c ON c.id = d.company_id WHERE c.clave = %s"
        " ORDER BY d.tipo, d.fuente, d.periodo, d.nombre_archivo",
        (clave,),
    ).fetchall()


async def test_ingesta_completa_de_una_empresa_con_cnv_hechos_y_sec(
    settings_with_universe: Settings, superuser: psycopg.Connection
) -> None:
    report = (await run(settings_with_universe, FakeSources(), only="GGAL"))["GGAL"]

    assert report.errores == []
    assert report.estados_estructurados == 2  # consolidados al 31/03 y al 30/06/2026
    rows = documents(superuser, "GGAL")
    assert [(r[0], r[1], r[3]) for r in rows] == [
        ("comunicado_resultados", "cnv", date(2026, 6, 30)),
        ("comunicado_resultados", "sec", date(2026, 6, 30)),
        ("estado_contable", "cnv", date(2026, 3, 31)),
        ("estado_contable", "cnv", date(2026, 6, 30)),
        ("resena_informativa", "cnv", date(2026, 3, 31)),
        ("resena_informativa", "cnv", date(2026, 6, 30)),
    ]
    # La memoria "NO CORRESPONDE" y los informes del auditor no se guardan.
    assert not [r for r in rows if "NO CORRESPONDE" in r[2]]
    # Los archivos existen en el almacenamiento.
    for *_, path in rows:
        assert (settings_with_universe.document_storage_dir / path).is_file()
    statements = superuser.execute(
        "SELECT presentacion_id, tipo_balance, unidad, jsonb_array_length(cuentas),"
        " document_id IS NOT NULL FROM cnv_statements ORDER BY presentacion_id"
    ).fetchall()
    assert statements == [
        (3525276, "consolidado", "Miles de $", 73, True),
        (3562190, "consolidado", "Miles de $", 73, True),
    ]
    # El 6-K que no es un comunicado queda registrado para no volver a bajarlo.
    assert superuser.execute("SELECT clave_externa, resultado FROM document_checks").fetchall() == [
        (f"sec:{OTHER_6K}:*", "no_es_comunicado")
    ]
    assert superuser.execute(
        "SELECT clave_externa FROM documents WHERE fuente = 'sec'"
    ).fetchone() == (f"sec:{RESULTS_6K}:pressrelease.htm",)


async def test_es_idempotente(
    settings_with_universe: Settings, superuser: psycopg.Connection
) -> None:
    await run(settings_with_universe, FakeSources())
    before = superuser.execute("SELECT count(*) FROM documents").fetchone()
    sources = FakeSources()

    reports = await run(settings_with_universe, sources)

    assert all(not r.nuevos and r.estados_estructurados == 0 for r in reports.values())
    assert superuser.execute("SELECT count(*) FROM documents").fetchone() == before
    # No vuelve a descargar nada: ni PDF de la CNV ni archivos de la SEC ya revisados.
    assert sources.requests["blob"] == 0
    assert sources.requests["sec_archivo"] == 0
    assert sources.requests["sitio_pdf"] == 0


async def test_sitios_de_inversores_filtran_por_patron_y_periodo(
    settings_with_universe: Settings, superuser: psycopg.Connection
) -> None:
    await run(settings_with_universe, FakeSources())

    tgn = [(r[0], r[2], r[3]) for r in documents(superuser, "TGN") if r[1] == "sitio_inversores"]
    assert tgn == [
        ("comunicado_resultados", "informe_de_resultados_1q_2026.pdf", date(2026, 3, 31)),
        ("comunicado_resultados", "informe_de_resultados_2q_2026.pdf", date(2026, 6, 30)),
    ]
    valo = [r[2] for r in documents(superuser, "VALO") if r[1] == "sitio_inversores"]
    assert valo == ["IR-2026Q2-ESP-Press-Release.pdf"]


async def test_una_fuente_caida_no_corta_a_las_demas(
    settings_with_universe: Settings, superuser: psycopg.Connection
) -> None:
    sources = FakeSources(failures={"/submissions/": httpx2.Response(503)})

    report = (await run(settings_with_universe, sources, only="GGAL"))["GGAL"]

    assert report.errores == [("sec", "http_503")]
    assert report.nuevos["estado_contable"] == 2
    assert [r for r in documents(superuser, "GGAL") if r[1] == "sec"] == []


async def test_un_cambio_de_formato_de_la_cnv_se_informa_como_error(
    settings_with_universe: Settings, superuser: psycopg.Connection
) -> None:
    sources = FakeSources(broken_views={"4eaadc7c-8f2b-478d-9234-eaf07dfd26cb"})

    report = (await run(settings_with_universe, sources, only="GGAL"))["GGAL"]

    assert ("cnv", "presentacion_sin_xml") in report.errores
    # El hecho relevante y la SEC se procesan igual.
    assert report.nuevos["comunicado_resultados"] == 2


async def test_empresa_no_sincronizada(
    settings_with_universe: Settings, superuser: psycopg.Connection
) -> None:
    superuser.execute("DELETE FROM companies WHERE clave = 'VALO'")

    report = (await run(settings_with_universe, FakeSources(), only="VALO"))["VALO"]

    assert report.errores == [("universo", "empresa_no_sincronizada")]
