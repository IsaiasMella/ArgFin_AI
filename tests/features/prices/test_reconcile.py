"""Validación cruzada, CCL implícito y configuración del tipo de cambio (T2.3)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from brujula.features.prices.daily import FX_FILE, PricesConfigError, load_fx_config
from brujula.features.prices.providers import DailyBar
from brujula.features.prices.reconcile import (
    Reconciled,
    divergence_pct,
    implied_rate,
    reconcile,
)

ROOT = Path(__file__).resolve().parents[3]
DAY = date(2026, 10, 6)
THRESHOLD = Decimal("2.5")


def bar(close: str, volume: str | None = "100", day: date = DAY) -> DailyBar:
    return DailyBar("GGAL", day, Decimal(close), None if volume is None else Decimal(volume))


def choose(primary: DailyBar | None, backup: DailyBar | None) -> Reconciled | None:
    return reconcile(
        primary, backup, primary_name="byma", backup_name="data912", threshold_pct=THRESHOLD
    )


def test_con_las_dos_fuentes_guarda_la_principal_y_la_divergencia() -> None:
    result = choose(bar("6165", "3417178"), bar("6200"))

    assert result is not None
    assert result.valor == Decimal(6165)
    assert result.volumen == Decimal(3417178)
    assert (result.fuente, result.valor_respaldo) == ("byma", Decimal(6200))
    assert result.divergencia_pct == Decimal("0.5677")
    assert result.marcado is False


@pytest.mark.parametrize(
    ("backup", "marked"),
    [
        ("102.5", False),  # justo en el umbral: no se marca (debe superarlo)
        ("102.5001", True),
        ("97.4", True),  # la divergencia es absoluta: también hacia abajo
    ],
)
def test_marca_cuando_la_divergencia_supera_el_umbral(backup: str, marked: bool) -> None:
    result = choose(bar("100"), bar(backup))

    assert result is not None
    assert result.marcado is marked


def test_con_una_sola_fuente_guarda_esa_sin_validacion_cruzada() -> None:
    only_primary = choose(bar("6165"), None)
    only_backup = choose(None, bar("6170"))

    assert only_primary is not None
    assert only_backup is not None
    assert (only_primary.fuente, only_primary.valor_respaldo, only_primary.marcado) == (
        "byma",
        None,
        False,
    )
    assert (only_backup.fuente, only_backup.valor, only_backup.divergencia_pct) == (
        "data912",
        Decimal(6170),
        None,
    )


def test_sin_ninguna_fuente_no_hay_rueda() -> None:
    assert choose(None, None) is None


def test_divergencia_redondeada_a_cuatro_decimales() -> None:
    assert divergence_pct(Decimal(3), Decimal(4)) == Decimal("33.3333")


def test_ccl_implicito_con_seis_decimales() -> None:
    # Cierres reales del 06/10/2026: AL30 85.840 pesos y AL30C 53,30 dólares cable.
    rate = implied_rate(bar("85840"), bar("53.3"))

    assert rate.cierre == Decimal("1610.506567")
    assert (rate.ticker, rate.fecha, rate.volumen) == ("CCL", DAY, None)


def test_ccl_con_ruedas_distintas_es_un_error() -> None:
    with pytest.raises(ValueError, match="misma rueda"):
        implied_rate(bar("85840"), bar("53.3", day=date(2026, 10, 5)))


def test_la_configuracion_versionada_del_ccl_es_valida() -> None:
    fx = load_fx_config(ROOT / "config" / FX_FILE)

    assert (fx.ccl.bono_pesos, fx.ccl.bono_cable) == ("AL30", "AL30C")


@pytest.mark.parametrize(
    "content",
    [
        "version: 1\nccl: {bono_pesos: AL30}\n",
        "version: 2\nccl: {bono_pesos: AL30, bono_cable: AL30C}\n",
        "version: 1\nccl: {bono_pesos: AL30, bono_cable: 'AL 30'}\n",
        "ccl: [",
    ],
)
def test_configuracion_invalida(tmp_path: Path, content: str) -> None:
    path = tmp_path / FX_FILE
    path.write_text(content, encoding="utf-8")

    with pytest.raises(PricesConfigError):
        load_fx_config(path)
