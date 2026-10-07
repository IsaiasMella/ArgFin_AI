"""Actualización de cifras XBRL de la SEC contra la base (T3.3)."""

from decimal import Decimal
from pathlib import Path

import httpx2
import psycopg
import pytest

from brujula.core.config import Settings
from brujula.features.financials.tasks import refresh_sec_facts
from brujula.features.universe.catalog import UNIVERSE_FILE, Universe, load_universe
from tests.support.env import VALID_ENV

pytestmark = pytest.mark.anyio

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "financials"
APPLE_URL = f"{VALID_ENV['SEC_DATA_BASE_URL']}/api/xbrl/companyfacts/CIK0000320193.json"


@pytest.fixture
def settings_with_apple(
    auth_settings: Settings, tmp_path: Path, superuser: psycopg.Connection
) -> Settings:
    versioned = load_universe(auth_settings.config_dir / UNIVERSE_FILE)
    apple = next(c for c in versioned.empresas if c.clave == "APPLE")
    universe = Universe.model_validate(
        {**versioned.model_dump(mode="json"), "empresas": [apple.model_dump(mode="json")]}
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / UNIVERSE_FILE).write_text(universe.model_dump_json(), encoding="utf-8")
    for name in ("metrics.yaml", "sec_xbrl.yaml"):
        (config_dir / name).write_text(
            (auth_settings.config_dir / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    superuser.execute(
        "INSERT INTO companies (clave, nombre, sector, pais, tipo)"
        " VALUES ('APPLE', 'Apple', 'Tecnología', 'US', 'cedear')"
    )
    return auth_settings.model_copy(update={"config_dir": config_dir})


def transport(status: int = 200) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) != APPLE_URL or status != 200:
            return httpx2.Response(status if status != 200 else 404)
        assert request.headers["User-Agent"] == VALID_ENV["SEC_USER_AGENT"]
        return httpx2.Response(200, content=(FIXTURE / "companyfacts_apple.json").read_bytes())

    return httpx2.MockTransport(handler)


async def test_guarda_cifras_trazables_y_es_idempotente(
    settings_with_apple: Settings, superuser: psycopg.Connection
) -> None:
    [first] = await refresh_sec_facts(settings_with_apple, http_transport=transport())
    [second] = await refresh_sec_facts(settings_with_apple, http_transport=transport())

    assert first.errores == []
    assert first.cifras == first.escritas > 0
    assert (second.cifras, second.escritas) == (first.cifras, 0)
    row = superuser.execute(
        "SELECT valor, moneda, unidad, base_medicion, fuente, referencia, formulario"
        " FROM financial_facts WHERE metrica = 'resultado_neto'"
        " AND periodo_inicio = '2026-03-29' AND periodo_fin = '2026-06-27'"
    ).fetchone()
    assert row is not None
    assert row[:5] == (Decimal(29789000000), "USD", "moneda", "nominal", "sec_xbrl")
    assert row[5].startswith("us-gaap:NetIncomeLoss 0000320193-26-")
    assert row[6] == "10-Q"


async def test_una_reexpresion_actualiza_el_valor(
    settings_with_apple: Settings, superuser: psycopg.Connection
) -> None:
    await refresh_sec_facts(settings_with_apple, http_transport=transport())
    superuser.execute(
        "UPDATE financial_facts SET valor = 1 WHERE metrica = 'activo_total'"
        " AND periodo_fin = '2026-06-27'"
    )

    [report] = await refresh_sec_facts(settings_with_apple, http_transport=transport())

    assert report.escritas == 1
    assert superuser.execute(
        "SELECT valor FROM financial_facts WHERE metrica = 'activo_total'"
        " AND periodo_fin = '2026-06-27'"
    ).fetchone() == (Decimal(383266000000),)


async def test_la_sec_caida_se_informa(
    settings_with_apple: Settings, superuser: psycopg.Connection
) -> None:
    [report] = await refresh_sec_facts(settings_with_apple, http_transport=transport(404))

    assert report.errores == [("sec_xbrl", "http_404")]
    assert superuser.execute("SELECT count(*) FROM financial_facts").fetchone() == (0,)
