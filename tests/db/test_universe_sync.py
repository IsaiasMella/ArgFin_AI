"""Sincronización del universo desde el YAML (T2.1)."""

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from brujula.cli import sync_universe_file
from brujula.core.config import Settings
from brujula.features.universe.catalog import UNIVERSE_FILE, Universe, load_universe
from brujula.features.universe.sync import SyncResult
from tests.db.conftest import Database
from tests.support.app import csrf_headers, login
from tests.support.google import FakeGoogle

COUNTS = (
    "SELECT (SELECT count(*) FROM companies WHERE activa),"
    " (SELECT count(*) FROM instruments WHERE activo)"
)


@pytest.fixture
def engine(database: Database, superuser: psycopg.Connection) -> Iterator[Engine]:
    engine = create_engine(database.url("migrator"))
    yield engine
    engine.dispose()


def _with_universe(settings: Settings, tmp_path: Path, universe: Universe) -> Settings:
    (tmp_path / UNIVERSE_FILE).write_text(universe.model_dump_json(), encoding="utf-8")
    return settings.model_copy(update={"config_dir": tmp_path})


def test_carga_el_universo_versionado_y_es_idempotente(
    settings: Settings, engine: Engine, superuser: psycopg.Connection
) -> None:
    first = sync_universe_file(settings, engine, dry_run=False)
    second = sync_universe_file(settings, engine, dry_run=False)

    assert (first.cambios.empresas_nuevas, first.cambios.instrumentos_nuevos) == (40, 40)
    assert second.cambios == SyncResult()
    assert superuser.execute(COUNTS).fetchone() == (40, 40)
    row = superuser.execute(
        "SELECT c.clave, c.tipo, c.cik_sec, i.ticker_origen, i.ratio_cedear"
        " FROM instruments i JOIN companies c ON c.id = i.company_id WHERE i.ticker_byma = 'AAPL'"
    ).fetchone()
    assert row == ("APPLE", "cedear", "0000320193", "AAPL", Decimal("20.0000"))


def test_simular_no_guarda_nada(
    settings: Settings, engine: Engine, superuser: psycopg.Connection
) -> None:
    report = sync_universe_file(settings, engine, dry_run=True)

    assert report.simulado
    assert report.cambios.empresas_nuevas == 40
    assert superuser.execute(COUNTS).fetchone() == (0, 0)


def test_actualiza_cambios_y_desactiva_lo_que_sale_del_archivo(
    settings: Settings, engine: Engine, superuser: psycopg.Connection, tmp_path: Path
) -> None:
    sync_universe_file(settings, engine, dry_run=False)
    universe = load_universe(settings.config_dir / UNIVERSE_FILE)
    apple = next(c for c in universe.empresas if c.clave == "APPLE")
    split = apple.model_copy(
        update={
            "instrumentos": [apple.instrumentos[0].model_copy(update={"ratio_cedear": Decimal(40)})]
        }
    )
    edited = universe.model_copy(
        update={
            "empresas": [
                split if c.clave == "APPLE" else c for c in universe.empresas if c.clave != "INTEL"
            ]
        }
    )

    report = sync_universe_file(_with_universe(settings, tmp_path, edited), engine, dry_run=False)

    assert report.cambios == SyncResult(
        instrumentos_actualizados=1, empresas_desactivadas=1, instrumentos_desactivados=1
    )
    assert superuser.execute(COUNTS).fetchone() == (39, 39)
    assert superuser.execute(
        "SELECT ratio_cedear FROM instruments WHERE ticker_byma = 'AAPL'"
    ).fetchone() == (Decimal("40.0000"),)
    # Volver a sincronizar el archivo original reactiva lo que había salido.
    back = sync_universe_file(settings, engine, dry_run=False)
    assert (back.cambios.empresas_actualizadas, back.cambios.instrumentos_actualizados) == (1, 2)
    assert superuser.execute(COUNTS).fetchone() == (40, 40)


def test_vincula_posiciones_cargadas_antes_de_que_el_ticker_entrara_al_universo(
    settings: Settings,
    engine: Engine,
    client: TestClient,
    google: FakeGoogle,
    superuser: psycopg.Connection,
) -> None:
    login(client, google)
    created = client.post(
        "/portfolio/holdings",
        json={"ticker": "GGAL", "cantidad": "10"},
        headers=csrf_headers(client),
    )
    assert created.json()["cobertura"] == "solo_precio"

    report = sync_universe_file(settings, engine, dry_run=False)

    assert report.posiciones_vinculadas == 1
    assert superuser.execute(
        "SELECT count(*) FROM holdings WHERE ticker_libre IS NULL AND instrument_id IS NOT NULL"
    ).fetchone() == (1,)
    [holding] = client.get("/portfolio/holdings").json()
    assert (holding["ticker"], holding["cobertura"]) == ("GGAL", "completa")


def test_un_instrumento_inactivo_pierde_la_cobertura_completa(
    settings: Settings,
    engine: Engine,
    client: TestClient,
    google: FakeGoogle,
    superuser: psycopg.Connection,
    tmp_path: Path,
) -> None:
    sync_universe_file(settings, engine, dry_run=False)
    login(client, google)
    client.post(
        "/portfolio/holdings",
        json={"ticker": "INTC", "cantidad": "3"},
        headers=csrf_headers(client),
    )
    universe = load_universe(settings.config_dir / UNIVERSE_FILE)
    without_intel = universe.model_copy(
        update={"empresas": [c for c in universe.empresas if c.clave != "INTEL"]}
    )

    sync_universe_file(_with_universe(settings, tmp_path, without_intel), engine, dry_run=False)

    [holding] = client.get("/portfolio/holdings").json()
    assert (holding["ticker"], holding["cobertura"]) == ("INTC", "solo_precio")
