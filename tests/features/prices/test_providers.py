"""Proveedores de precios con respuestas grabadas, sin llamadas reales (T2.2)."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx2
import pytest

from brujula.core.config import Settings
from brujula.features.prices.providers import (
    AssetKind,
    BymaOpenData,
    Data912,
    PriceProvider,
    Symbol,
)
from tests.support.markets import RecordedMarkets

pytestmark = pytest.mark.anyio

GGAL = Symbol("GGAL", AssetKind.EQUITY)
AAPL = Symbol("AAPL", AssetKind.CEDEAR)
AL30 = Symbol("AL30", AssetKind.BOND)
AL30C = Symbol("AL30C", AssetKind.BOND)
OCT_1, OCT_6 = date(2026, 10, 1), date(2026, 10, 6)
# Ruedas grabadas entre el 1/10 y el 6/10/2026 (el 3 y el 4 fueron fin de semana).
TRADING_DAYS = [date(2026, 10, d) for d in (1, 2, 5, 6)]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def markets() -> RecordedMarkets:
    return RecordedMarkets()


@pytest.fixture
async def http(markets: RecordedMarkets) -> AsyncIterator[httpx2.AsyncClient]:
    async with httpx2.AsyncClient(transport=markets.transport()) as client:
        yield client


@pytest.fixture
def byma(settings: Settings, http: httpx2.AsyncClient) -> BymaOpenData:
    return BymaOpenData(settings, http, retry_delay_seconds=0)


@pytest.fixture
def data912(settings: Settings, http: httpx2.AsyncClient) -> Data912:
    return Data912(settings, http, retry_delay_seconds=0)


async def test_byma_devuelve_las_ruedas_del_rango_con_decimales_exactos(
    byma: BymaOpenData, markets: RecordedMarkets
) -> None:
    result = await byma.daily_bars([GGAL, AL30C], OCT_1, OCT_6)

    assert result.errors == {}
    assert [b.fecha for b in result.bars["GGAL"]] == TRADING_DAYS
    assert [b.cierre for b in result.bars["GGAL"]] == [5865, 5835, 6135, 6165]
    assert result.bars["AL30C"][-1].cierre == Decimal("53.3")  # sin redondeo de float
    assert result.bars["GGAL"][0].volumen == Decimal(5552826)
    params = markets.byma_params[0]
    assert params["symbol"] in {"GGAL 24HS", "AL30C 24HS"}
    assert params["resolution"] == "D"
    assert int(params["from"]) == int(datetime(2026, 10, 1, tzinfo=UTC).timestamp())
    assert int(params["to"]) == int(datetime(2026, 10, 7, tzinfo=UTC).timestamp())


async def test_data912_filtra_el_rango_de_su_historia_completa(data912: Data912) -> None:
    result = await data912.daily_bars([GGAL, AAPL, AL30], OCT_1, OCT_6)

    assert result.errors == {}
    assert {t: [b.fecha for b in bars] for t, bars in result.bars.items()} == {
        "GGAL": TRADING_DAYS,
        "AAPL": TRADING_DAYS,
        "AL30": TRADING_DAYS,
    }


async def test_las_dos_fuentes_coinciden_en_las_respuestas_grabadas(
    byma: BymaOpenData, data912: Data912
) -> None:
    symbols = [GGAL, AAPL, AL30, AL30C]

    official = await byma.daily_bars(symbols, OCT_1, OCT_6)
    backup = await data912.daily_bars(symbols, OCT_1, OCT_6)

    for symbol in symbols:
        assert [(b.fecha, b.cierre) for b in official.bars[symbol.ticker]] == [
            (b.fecha, b.cierre) for b in backup.bars[symbol.ticker]
        ]


@pytest.mark.parametrize("provider_name", ["byma", "data912"])
async def test_ticker_desconocido_no_corta_a_los_demas(
    provider_name: str, byma: BymaOpenData, data912: Data912
) -> None:
    provider: PriceProvider = byma if provider_name == "byma" else data912

    result = await provider.daily_bars([GGAL, Symbol("NOEXISTE", AssetKind.EQUITY)], OCT_1, OCT_6)

    assert len(result.bars["GGAL"]) == 4
    if provider_name == "byma":  # BYMA responde "no_data": no hay ruedas, no es un error
        assert (result.bars["NOEXISTE"], result.errors) == ([], {})
    else:
        assert result.errors == {"NOEXISTE": "ticker_desconocido"}


async def test_reintenta_fallas_transitorias(byma: BymaOpenData, markets: RecordedMarkets) -> None:
    ok = httpx2.Response(200, content=b'{"s":"ok","t":[1790823600],"c":[5865],"v":[1]}')
    markets.script("byma", "GGAL", httpx2.Response(503), httpx2.Response(429), ok)

    result = await byma.daily_bars([GGAL], OCT_1, OCT_6)

    assert [b.cierre for b in result.bars["GGAL"]] == [5865]
    assert markets.requests[("byma", "GGAL")] == 3


async def test_se_rinde_despues_de_tres_intentos(
    byma: BymaOpenData, markets: RecordedMarkets
) -> None:
    markets.script("byma", "GGAL", httpx2.Response(502))

    result = await byma.daily_bars([GGAL], OCT_1, OCT_6)

    assert result.errors == {"GGAL": "http_502"}
    assert markets.requests[("byma", "GGAL")] == 3


async def test_error_definitivo_no_se_reintenta(data912: Data912, markets: RecordedMarkets) -> None:
    markets.script("data912", "GGAL", httpx2.Response(404))

    result = await data912.daily_bars([GGAL], OCT_1, OCT_6)

    assert result.errors == {"GGAL": "http_404"}
    assert markets.requests[("data912", "GGAL")] == 1


async def test_sin_conexion(settings: Settings) -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("sin red", request=request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(fail)) as client:
        provider = BymaOpenData(settings, client, retry_delay_seconds=0)
        result = await provider.daily_bars([GGAL], OCT_1, OCT_6)

    assert result.errors == {"GGAL": "sin_conexion"}


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"<html>mantenimiento</html>", "respuesta_no_json"),
        (b'{"s":"ok","t":[1790823600],"c":[0],"v":[1]}', "cierre_invalido"),
        (b'{"s":"ok","t":[1790823600],"c":[-5],"v":[1]}', "cierre_invalido"),
        (b'{"s":"ok","t":[1790823600],"c":["5865"],"v":[1]}', "valor_no_numerico"),
        (b'{"s":"ok","t":[1790823600],"c":[5865,1],"v":[1]}', "formato_inesperado"),
        (b'{"s":"ok","t":["ayer"],"c":[5865],"v":[1]}', "formato_inesperado"),
        (b'{"s":"error"}', "formato_inesperado"),
        (b"[1, 2]", "formato_inesperado"),
    ],
)
async def test_respuestas_invalidas_de_byma(
    byma: BymaOpenData, markets: RecordedMarkets, body: bytes, reason: str
) -> None:
    markets.script("byma", "GGAL", httpx2.Response(200, content=body))

    result = await byma.daily_bars([GGAL], OCT_1, OCT_6)

    assert result.errors == {"GGAL": reason}
    assert "GGAL" not in result.bars


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b'[{"date":"2026-10-01","c":null,"v":1}]', "valor_no_numerico"),
        (b'[{"date":"01/10/2026","c":5865,"v":1}]', "formato_inesperado"),
        (b'[{"c":5865}]', "formato_inesperado"),
        (b'{"s":"ok"}', "formato_inesperado"),
    ],
)
async def test_respuestas_invalidas_de_data912(
    data912: Data912, markets: RecordedMarkets, body: bytes, reason: str
) -> None:
    markets.script("data912", "GGAL", httpx2.Response(200, content=body))

    result = await data912.daily_bars([GGAL], OCT_1, OCT_6)

    assert result.errors == {"GGAL": reason}


async def test_ticker_con_caracteres_especiales_va_codificado_en_la_url(
    data912: Data912, markets: RecordedMarkets
) -> None:
    await data912.daily_bars([Symbol("BRK.B/X", AssetKind.CEDEAR)], OCT_1, OCT_6)

    assert markets.requests[("data912", "BRK.B/X")] == 1
