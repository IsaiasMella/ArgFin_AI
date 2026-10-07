"""`config/universe.yaml`: el archivo versionado es válido y el esquema rechaza errores (T2.1)."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from brujula.features.universe.catalog import UNIVERSE_FILE, UniverseError, load_universe

ROOT = Path(__file__).resolve().parents[3]
VERSIONED = ROOT / "config" / UNIVERSE_FILE


def test_el_universo_versionado_tiene_20_acciones_y_20_cedears() -> None:
    universe = load_universe(VERSIONED)

    assert (universe.count("ar_equity"), universe.count("cedear")) == (20, 20)


def test_cada_ticker_figura_en_el_ranking_que_justifica_su_eleccion() -> None:
    universe = load_universe(VERSIONED)
    ranking = universe.seleccion.ranking_millones_ars

    for group, tipo in (("acciones", "ar_equity"), ("cedears", "cedear")):
        tickers = {
            instrument.ticker_byma
            for company in universe.empresas
            if company.tipo == tipo
            for instrument in company.instrumentos
        }
        assert tickers == set(ranking[group])
    # Ningún excluido (ETF) se coló en el universo.
    assert not set(universe.seleccion.excluidos) & set(ranking["cedears"])


def test_toda_empresa_argentina_tiene_su_fuente_documentada_en_la_cnv() -> None:
    # Criterio de T3.1: fuente documentada para al menos 10 empresas antes de seguir.
    universe = load_universe(VERSIONED)
    argentine = [c for c in universe.empresas if c.tipo == "ar_equity"]

    assert len(argentine) == 20
    assert all(c.cnv is not None for c in argentine)
    assert len({c.cnv.cuit for c in argentine if c.cnv}) == 20
    with_site = {c.clave for c in argentine if c.inversores}
    assert with_site == {"TRANSENER", "BYMA", "TGN", "BANCO_VALORES", "ECOGAS"}


def test_la_ventana_de_seleccion_cierra_antes_de_la_fecha_de_corte() -> None:
    selection = load_universe(VERSIONED).seleccion

    assert selection.ventana.hasta < selection.fecha_de_corte


# --- Errores del esquema -----------------------------------------------------------------


def _base() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(VERSIONED.read_text(encoding="utf-8"))
    raw["empresas"] = [
        {
            "clave": "GGAL",
            "nombre": "Galicia",
            "tipo": "ar_equity",
            "sector": "Financiero",
            "pais": "AR",
            "cik_sec": "0001114700",
            "cnv": {
                "cuit": "30704962807",
                "id": 3353,
                "balance": "consolidado",
                "cierre_ejercicio": "12-31",
            },
            "comunicados": ["cnv_hecho_relevante", "sec_6k"],
            "instrumentos": [{"ticker_byma": "GGAL", "moneda": "ARS"}],
        },
        {
            "clave": "APPLE",
            "nombre": "Apple",
            "tipo": "cedear",
            "sector": "Tecnología",
            "pais": "US",
            "cik_sec": "0000320193",
            "instrumentos": [
                {
                    "ticker_byma": "AAPL",
                    "ticker_origen": "AAPL",
                    "ratio_cedear": 20,
                    "moneda": "ARS",
                }
            ],
        },
    ]
    return raw


def _write(tmp_path: Path, raw: dict[str, Any]) -> Path:
    path = tmp_path / UNIVERSE_FILE
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def test_un_archivo_minimo_valido_carga_y_normaliza_tickers(tmp_path: Path) -> None:
    raw = _base()
    raw["empresas"][0]["instrumentos"][0]["ticker_byma"] = " ggal "

    universe = load_universe(_write(tmp_path, raw))

    assert universe.empresas[0].instrumentos[0].ticker_byma == "GGAL"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r["empresas"][1]["instrumentos"][0].pop("ratio_cedear"), "necesita ratio"),
        (lambda r: r["empresas"][0]["instrumentos"][0].update(ratio_cedear=1), "no lleva ratio"),
        (lambda r: r["empresas"][1].update(clave="GGAL"), "claves repetidas: GGAL"),
        (
            lambda r: r["empresas"][1]["instrumentos"][0].update(ticker_byma="GGAL"),
            "tickers repetidas: GGAL",
        ),
        (lambda r: r["empresas"][1].update(cik_sec="320193"), "cik_sec"),
        (lambda r: r["empresas"][0].update(tipo="bono"), "tipo"),
        (lambda r: r["empresas"][0].update(precio_objetivo=1), "precio_objetivo"),
        (lambda r: r["seleccion"].pop("fuente"), "fuente"),
        (lambda r: r["empresas"][0].pop("cnv"), "necesita su ficha de la CNV"),
        (lambda r: r["empresas"][1].update(cnv=r["empresas"][0]["cnv"]), "no tiene ficha"),
        (lambda r: r["empresas"][0].pop("cik_sec"), "necesitan cik_sec"),
        (lambda r: r["empresas"][0]["cnv"].update(cuit="30-70496280-7"), "cuit"),
        (lambda r: r["empresas"][0]["cnv"].update(cierre_ejercicio="13-31"), "cierre_ejercicio"),
        (lambda r: r["empresas"][0]["cnv"].update(balance="ambos"), "balance"),
        (lambda r: r["empresas"][0].update(comunicados=["twitter"]), "comunicados"),
        (
            lambda r: r["empresas"][0].update(comunicados=["sitio_inversores"]),
            "van juntos",
        ),
        (
            lambda r: r["empresas"][0].update(
                comunicados=["sitio_inversores"],
                inversores={"url": "https://x.com", "acceso": "enlaces_pdf", "patron": "(abc"},
            ),
            "expresión regular",
        ),
        (
            lambda r: r["empresas"][0].update(
                comunicados=["sitio_inversores"],
                inversores={"url": "http://x.com", "acceso": "enlaces_pdf", "patron": "abc"},
            ),
            "url",
        ),
    ],
)
def test_errores_de_esquema(tmp_path: Path, change: Any, message: str) -> None:
    raw = _base()
    change(raw)

    with pytest.raises(UniverseError, match=message):
        load_universe(_write(tmp_path, raw))


def test_archivo_inexistente(tmp_path: Path) -> None:
    with pytest.raises(UniverseError, match="no se pudo leer"):
        load_universe(tmp_path / "no-existe.yaml")


def test_yaml_invalido(tmp_path: Path) -> None:
    path = tmp_path / UNIVERSE_FILE
    path.write_text("empresas: [", encoding="utf-8")

    with pytest.raises(UniverseError, match="no es YAML válido"):
        load_universe(path)
