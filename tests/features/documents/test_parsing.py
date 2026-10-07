"""Lectura de la CNV, texto de documentos, períodos y configuración de la ingesta (T3.2)."""

from datetime import date
from pathlib import Path

import pytest

from brujula.features.documents.settings import (
    DOCUMENTS_FILE,
    DocumentsConfigError,
    load_documents_config,
)
from brujula.features.documents.sources.cnv import (
    CnvFormatError,
    own_statement,
    parse_listing,
    parse_presentation,
    parse_spanish_date,
    presentation_date,
)
from brujula.features.documents.sources.investor_sites import pdf_links
from brujula.features.documents.text import document_text, html_text, infer_period
from tests.support.documents import FIXTURES, pdf

ROOT = Path(__file__).resolve().parents[3]
AIF = "https://aif2.cnv.gov.ar"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- CNV ---------------------------------------------------------------------------------


def test_listado_de_estados_contables_grabado() -> None:
    listings = parse_listing(_read("cnv_listado_estados.html"), AIF)

    refs = [own_statement(listing) for listing in listings]
    own = [(r.tipo_balance, r.periodicidad, r.fecha_cierre) for r in refs if r is not None]
    assert own == [
        ("individual", "trimestral", date(2026, 6, 30)),
        ("consolidado", "trimestral", date(2026, 6, 30)),
        ("individual", "trimestral", date(2026, 3, 31)),
        ("consolidado", "trimestral", date(2026, 3, 31)),
    ]
    # Las presentaciones de controladas ("RELAC.: CONTROLADA ...") no son de la empresa.
    assert sum(r is None for r in refs) == 2
    assert listings[1].presentacion_id == 3562190
    assert listings[1].fecha == date(2026, 8, 26)


def test_links_de_otro_host_no_se_toman_como_presentaciones() -> None:
    assert parse_listing(_read("cnv_listado_estados.html"), "https://otro.example") == []


def test_presentacion_de_estado_contable_grabada() -> None:
    presentation = parse_presentation(_read("aif_estado_contable.html"))

    props = presentation.propiedades
    assert presentation_date(props["FechaCierre"]) == date(2026, 6, 30)
    assert (props["TipoBalance"], props["UnidadMedida"]) == ("Individual", "Miles de $")
    assert len(presentation.cuentas) == 73
    assert {"nro": "1999999", "rubro": "TOTAL DEL ACTIVO", "monto": "9388201890.00"} in (
        presentation.cuentas
    )
    kinds = [a.propiedad for a in presentation.adjuntos]
    assert kinds == [
        "ABEstadoContable",
        "ABMemoria",
        "ABInformeAuditorIndependiente",
        "ABInformeComisionFiscalizadoraSindico",
        "ABResenaInformativa",
    ]


def test_presentacion_de_hecho_relevante_grabada() -> None:
    presentation = parse_presentation(_read("aif_hecho_relevante.html"))

    [attachment] = presentation.adjuntos
    assert attachment.nombre == "PressRelease_GrupoGalicia_2Q2026.pdf"
    assert "RESULTADOS TRIMESTRALES 2T 2026" in presentation.propiedades["Descripcion"]


@pytest.mark.parametrize(
    ("page", "reason"),
    [
        ("<html>mantenimiento</html>", "presentacion_sin_xml"),
        ("var presentation = '<modeloDatos><sin cerrar';\n", "presentacion_xml_invalido"),
    ],
)
def test_cambio_de_formato_de_la_cnv(page: str, reason: str) -> None:
    with pytest.raises(CnvFormatError, match=reason):
        parse_presentation(page)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("25 ago. 2026", date(2026, 8, 25)),
        ("6 oct. 2026", date(2026, 10, 6)),
        ("13 may 2026", date(2026, 5, 13)),
        ("1 sept. 2026", date(2026, 9, 1)),
        ("ayer", None),
    ],
)
def test_fechas_de_la_cnv(text: str, expected: date | None) -> None:
    assert parse_spanish_date(text) == expected


# --- Texto y períodos --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Transener2Q26_VF.pdf", date(2026, 6, 30)),
        ("BYMA-Comunicado_de_Prensa-2T26.pdf", date(2026, 6, 30)),
        ("informe_de_resultados_1q_2026.pdf", date(2026, 3, 31)),
        ("IR-2026Q2-ESP-Press-Release.pdf", date(2026, 6, 30)),
        ("RESULTADOS TRIMESTRALES 4T 2025", date(2025, 12, 31)),
        ("NOTA DE RESULTADOS 2° TRIMESTRE 2026", date(2026, 6, 30)),
        ("results for the third quarter of 2025", date(2025, 9, 30)),
        ("resultados correspondientes al primer trimestre 2026", date(2026, 3, 31)),
        ("COVID-19 en 2026", None),
        ("Memoria anual", None),
    ],
)
def test_periodo_inferido(text: str, expected: date | None) -> None:
    assert infer_period(text) == expected


def test_texto_de_html_sin_scripts_ni_etiquetas() -> None:
    page = b"<html><script>var x=1</script><p>Resultados &amp; datos</p></html>"

    assert html_text(page) == "Resultados & datos"


def test_texto_de_pdf() -> None:
    assert "Hola balance" in document_text(pdf("Hola balance"), "x.PDF")


# --- Sitios de inversores ----------------------------------------------------------------


def test_links_pdf_filtrados_por_patron_y_sin_repetidos() -> None:
    page = (
        '<a href="/d/informe_de_resultados_2q_2026.pdf">a</a>'
        '<a href="/d/informe_de_resultados_2q_2026.pdf">b</a>'
        '<a href="https://otro.example/d/EEFF.pdf">c</a>'
        '<a href="/d/informe_de_resultados_1q_2026.htm">d</a>'
    )

    docs = pdf_links(page, "https://site.example/inversores/", r"informe_de_resultados_\dq_\d{4}")

    assert [d.url for d in docs] == ["https://site.example/d/informe_de_resultados_2q_2026.pdf"]


# --- Configuración -----------------------------------------------------------------------


def test_la_configuracion_versionada_es_valida_y_reconoce_resultados() -> None:
    config = load_documents_config(ROOT / "config" / DOCUMENTS_FILE)

    assert config.cnv.is_results_fact("INFORMACIÓN FINANCIERA - YPF - RESULTADOS 2° TRIMESTRE 2026")
    assert not config.cnv.is_results_fact("ANUNCIO FECHA DE RESULTADOS 2DO TRIMESTRE 2026")
    assert not config.cnv.is_results_fact("RESULTADOS DE LA OFERTA DE COMPRA - TRIMESTRE")
    assert config.cnv.ignores("NO CORRESPONDE ARCHIVO OBLIGATORIO.pdf")
    assert config.sec.is_results_release("Pampa announces 2Q26 results")
    assert config.sec.is_results_release("results for the second quarter of 2026")
    assert config.sec.is_results_release(
        "Telecom Argentina S.A. announces consolidated results for the first half (1H26)"
    )
    assert not config.sec.is_results_release("Notice of shareholders meeting")


def test_configuracion_con_regex_invalida(tmp_path: Path) -> None:
    text = (ROOT / "config" / DOCUMENTS_FILE).read_text(encoding="utf-8")
    path = tmp_path / DOCUMENTS_FILE
    path.write_text(text.replace("ignorar_archivo: '", "ignorar_archivo: '(", 1), encoding="utf-8")

    with pytest.raises(DocumentsConfigError, match="expresión regular inválida"):
        load_documents_config(path)
