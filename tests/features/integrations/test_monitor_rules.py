"""Reglas puras del monitor de integraciones: plazos de la CNV y tipos de falla (T3.7)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from brujula.core.http import FetchError, FormatError
from brujula.core.integrations import SourceRun
from brujula.features.documents.sources.cnv import CnvFormatError
from brujula.features.integrations.service import (
    CnvDeadlines,
    expected_statement,
    load_monitor_config,
    quarter_ends,
)
from brujula.features.prices.daily import provider_run
from brujula.features.prices.providers import DailyBar, FetchResult

CONFIG = Path(__file__).resolve().parents[3] / "config"
DEADLINES = CnvDeadlines(anual_dias=70, trimestral_dias=42, margen_dias=5)


def test_la_configuracion_versionada_es_valida() -> None:
    config = load_monitor_config(CONFIG)

    assert config.fallas_consecutivas == 3
    assert config.plazos_cnv == DEADLINES


def test_cierres_trimestrales_hacia_atras() -> None:
    assert quarter_ends("12-31", date(2026, 10, 8)) == [
        date(2026, 9, 30),
        date(2026, 6, 30),
        date(2026, 3, 31),
        date(2025, 12, 31),
    ]
    # El mismo día del cierre todavía no cuenta.
    assert quarter_ends("06-30", date(2026, 6, 30))[0] == date(2026, 3, 31)


@pytest.mark.parametrize(
    ("fiscal_end", "today", "expected"),
    [
        # Trimestral al 30/06: vence a los 42 días (11/8), se avisa pasado el margen (16/8).
        ("12-31", date(2026, 8, 16), date(2026, 3, 31)),
        ("12-31", date(2026, 8, 17), date(2026, 6, 30)),
        # Anual al 31/12: 70 días (11/3) más el margen.
        ("12-31", date(2026, 3, 16), date(2025, 9, 30)),
        ("12-31", date(2026, 3, 17), date(2025, 12, 31)),
        # Aluar cierra el ejercicio el 30/06: ese es su anual.
        ("06-30", date(2026, 9, 13), date(2026, 3, 31)),
        ("06-30", date(2026, 9, 14), date(2026, 6, 30)),
    ],
)
def test_ultimo_estado_vencido(fiscal_end: str, today: date, expected: date) -> None:
    assert expected_statement(fiscal_end, today, DEADLINES) == expected


def test_un_cambio_de_formato_se_distingue_de_una_falla() -> None:
    assert SourceRun.from_error("sec", "GGAL", FetchError("http_503")) == SourceRun(
        "sec", "GGAL", "http_503", formato=False
    )
    assert SourceRun.from_error("sitio", "BYMA", FormatError("wordpress_formato_inesperado"))
    assert SourceRun.from_error("cnv", "YPF", CnvFormatError("presentacion_sin_xml")).formato
    assert SourceRun("cnv", "YPF").ok


def test_un_proveedor_de_precios_falla_solo_si_no_trajo_nada() -> None:
    bar = DailyBar(ticker="GGAL", fecha=date(2026, 10, 7), cierre=Decimal(1), volumen=None)

    partial = FetchResult(bars={"GGAL": [bar]}, errors={"YPF": "http_404"})
    assert provider_run("byma", partial) == SourceRun("precios:byma")

    down = FetchResult(errors={"GGAL": "http_503", "YPF": "http_503", "PAMPA": "http_404"})
    assert provider_run("byma", down) == SourceRun("precios:byma", "", "http_503", False)

    changed = FetchResult(errors={"GGAL": "formato_inesperado"})
    assert provider_run("data912", changed).formato
