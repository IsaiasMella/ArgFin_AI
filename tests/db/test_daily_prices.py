"""Tarea diaria de precios y CCL contra la base, con fuentes grabadas (T2.3).

Criterio: idempotente; un dato marcado no aparece en informes.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import httpx2
import psycopg
import pytest

from brujula.core.config import Settings
from brujula.core.db import create_engine, create_session_factory
from brujula.features.prices.daily import DailyRunReport, PricesUnavailableError
from brujula.features.prices.service import publishable_ccl, publishable_closes
from brujula.features.prices.tasks import update_prices
from tests.support.markets import RecordedMarkets

pytestmark = pytest.mark.anyio

START, END = date(2026, 9, 28), date(2026, 10, 6)
# Ruedas grabadas en esa ventana (el 3 y el 4 de octubre fueron fin de semana).
TRADING_DAYS = [START + timedelta(days=d) for d in (0, 1, 2, 3, 4, 7, 8)]


def seed(conn: psycopg.Connection, ticker: str, tipo: str, active: bool = True) -> UUID:
    row = conn.execute(
        "INSERT INTO companies (clave, nombre, sector, pais, tipo)"
        " VALUES (%s, %s, 'Prueba', 'AR', %s) RETURNING id",
        (ticker, ticker, tipo),
    ).fetchone()
    assert row is not None
    instrument = conn.execute(
        "INSERT INTO instruments (company_id, ticker_byma, moneda, activo)"
        " VALUES (%s, %s, 'ARS', %s) RETURNING id",
        (row[0], ticker, active),
    ).fetchone()
    assert instrument is not None
    instrument_id: UUID = instrument[0]
    return instrument_id


@pytest.fixture
def universe(superuser: psycopg.Connection) -> dict[str, UUID]:
    return {"GGAL": seed(superuser, "GGAL", "ar_equity"), "AAPL": seed(superuser, "AAPL", "cedear")}


async def run(
    settings: Settings, markets: RecordedMarkets, start: date = START, end: date = END
) -> DailyRunReport:
    return await update_prices(settings, start=start, end=end, http_transport=markets.transport())


def prices(conn: psycopg.Connection, ticker: str) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT p.fecha, p.cierre, p.fuente, p.cierre_respaldo, p.divergencia_pct, p.marcado"
        " FROM prices_daily p JOIN instruments i ON i.id = p.instrument_id"
        " WHERE i.ticker_byma = %s ORDER BY p.fecha",
        (ticker,),
    ).fetchall()


async def test_guarda_cierres_validados_y_el_ccl_de_cada_rueda(
    auth_settings: Settings, superuser: psycopg.Connection, universe: dict[str, UUID]
) -> None:
    report = await run(auth_settings, RecordedMarkets())

    ggal = prices(superuser, "GGAL")
    assert [row[0] for row in ggal] == TRADING_DAYS
    assert ggal[-1] == (END, Decimal(6165), "byma", Decimal(6165), Decimal(0), False)
    assert len(prices(superuser, "AAPL")) == 7
    assert superuser.execute(
        "SELECT ccl, fuente, ccl_respaldo, marcado FROM fx_daily WHERE fecha = %s", (END,)
    ).fetchone() == (Decimal("1610.506567"), "byma", Decimal("1610.506567"), False)
    assert (report.precios_guardados, report.ccl_guardados, report.precios_marcados) == (14, 7, 0)
    assert report.errores == {}


async def test_es_idempotente(
    auth_settings: Settings, superuser: psycopg.Connection, universe: dict[str, UUID]
) -> None:
    await run(auth_settings, RecordedMarkets())
    before = superuser.execute(
        "SELECT instrument_id, fecha, actualizado_en FROM prices_daily ORDER BY 1, 2"
    ).fetchall()

    again = await run(auth_settings, RecordedMarkets())

    assert again.precios_cambiados == 0
    after = superuser.execute(
        "SELECT instrument_id, fecha, actualizado_en FROM prices_daily ORDER BY 1, 2"
    ).fetchall()
    assert after == before  # ni filas nuevas ni actualizaciones
    assert superuser.execute("SELECT count(*) FROM fx_daily").fetchone() == (7,)


async def test_un_dato_marcado_no_aparece_para_los_informes(
    auth_settings: Settings, superuser: psycopg.Connection, universe: dict[str, UUID]
) -> None:
    markets = RecordedMarkets()
    markets.data912_closes[("GGAL", "2026-10-06")] = 6500  # +5,4 %: supera el umbral de 2,5 %
    markets.data912_closes[("AL30C", "2026-10-05")] = 60  # el CCL de data912 diverge ese día

    report = await run(auth_settings, markets)

    assert prices(superuser, "GGAL")[-1][3:] == (Decimal(6500), Decimal("5.4339"), True)
    assert (report.precios_marcados, report.ccl_marcados) == (1, 1)
    engine = create_engine(auth_settings)
    try:
        async with create_session_factory(engine)() as session:
            closes = await publishable_closes(session, [universe["GGAL"]], START, END)
            ccl = await publishable_ccl(session, START, END)
    finally:
        await engine.dispose()
    ggal_days = sorted(closes[universe["GGAL"]])
    assert ggal_days == TRADING_DAYS[:-1]  # el 06/10 marcado queda afuera
    assert sorted(ccl) == [d for d in TRADING_DAYS if d != date(2026, 10, 5)]


async def test_si_la_principal_falla_usa_el_respaldo_y_despues_se_corrige(
    auth_settings: Settings, superuser: psycopg.Connection, universe: dict[str, UUID]
) -> None:
    markets = RecordedMarkets()
    markets.script("byma", "GGAL", httpx2.Response(500))

    degraded = await run(auth_settings, markets)

    assert degraded.errores == {"byma": {"GGAL": "http_500"}}
    assert degraded.precios_solo_respaldo == 7
    assert {(row[2], row[3], row[5]) for row in prices(superuser, "GGAL")} == {
        ("data912", None, False)
    }

    # La corrida siguiente revisa la misma ventana y reemplaza el dato por el oficial.
    recovered = await run(auth_settings, RecordedMarkets())

    assert recovered.precios_cambiados == 7
    assert {row[2] for row in prices(superuser, "GGAL")} == {"byma"}


async def test_una_falla_de_la_principal_no_pisa_datos_oficiales_ya_guardados(
    auth_settings: Settings, superuser: psycopg.Connection, universe: dict[str, UUID]
) -> None:
    await run(auth_settings, RecordedMarkets())
    before = prices(superuser, "GGAL")
    markets = RecordedMarkets()
    markets.script("byma", "GGAL", httpx2.Response(500))
    markets.script("byma", "AL30C", httpx2.Response(500))

    degraded = await run(auth_settings, markets)

    assert degraded.precios_cambiados == 0
    assert prices(superuser, "GGAL") == before
    assert superuser.execute("SELECT DISTINCT fuente FROM fx_daily").fetchall() == [("byma",)]


async def test_feriado_o_fin_de_semana_no_guarda_nada(
    auth_settings: Settings, superuser: psycopg.Connection, universe: dict[str, UUID]
) -> None:
    sunday = date(2026, 10, 4)

    report = await run(auth_settings, RecordedMarkets(), start=sunday, end=sunday)

    assert (report.precios_guardados, report.ccl_guardados, report.errores) == (0, 0, {})
    assert superuser.execute("SELECT count(*) FROM prices_daily").fetchone() == (0,)


async def test_sin_ninguna_fuente_la_tarea_falla_para_reintentarse(
    auth_settings: Settings, superuser: psycopg.Connection, universe: dict[str, UUID]
) -> None:
    markets = RecordedMarkets()
    for ticker in ("GGAL", "AAPL", "AL30", "AL30C"):
        markets.script("byma", ticker, httpx2.Response(503))
        markets.script("data912", ticker, httpx2.Response(503))

    with pytest.raises(PricesUnavailableError, match="sin datos de ninguna fuente"):
        await run(auth_settings, markets)
    assert superuser.execute("SELECT count(*) FROM prices_daily").fetchone() == (0,)


async def test_no_pide_instrumentos_inactivos(
    auth_settings: Settings, superuser: psycopg.Connection, universe: dict[str, UUID]
) -> None:
    seed(superuser, "VIEJO", "ar_equity", active=False)
    markets = RecordedMarkets()

    await run(auth_settings, markets)

    assert ("byma", "VIEJO") not in markets.requests
    assert ("byma", "GGAL") in markets.requests
