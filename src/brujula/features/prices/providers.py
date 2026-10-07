"""Proveedores de precios de cierre diarios (T2.2, ADR 011).

Cada proveedor implementa `PriceProvider`: recibe especies y un rango de fechas y devuelve las
ruedas que encontró, más un error por especie para las que no pudo traer. Un proveedor nunca
inventa una rueda: si la fuente no la tiene, no aparece.
"""

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import quote

import anyio
import httpx2
import structlog

from brujula.core.config import Settings

logger = structlog.get_logger(__name__)

MAX_ATTEMPTS = 3
MAX_CONCURRENT_REQUESTS = 4
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
DATA912_PATHS = MappingProxyType({"accion": "stocks", "cedear": "cedears", "bono": "bonds"})


class AssetKind(StrEnum):
    EQUITY = "accion"
    CEDEAR = "cedear"
    BOND = "bono"


@dataclass(frozen=True)
class Symbol:
    ticker: str
    kind: AssetKind


@dataclass(frozen=True)
class DailyBar:
    ticker: str
    fecha: date
    cierre: Decimal
    volumen: Decimal | None


@dataclass
class FetchResult:
    bars: dict[str, list[DailyBar]] = field(default_factory=dict)
    # Especies que no se pudieron traer, con el motivo (para logs y alertas).
    errors: dict[str, str] = field(default_factory=dict)


class PriceProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def daily_bars(self, symbols: Sequence[Symbol], start: date, end: date) -> FetchResult:
        """Ruedas con fecha entre `start` y `end` (inclusive), ordenadas por fecha."""
        ...


class SymbolError(Exception):
    """No se pudo obtener una especie. `reason` es corto y no contiene datos sensibles."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise SymbolError("valor_no_numerico")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise SymbolError("valor_no_numerico") from None


def _bar(ticker: str, day: date, close: Any, volume: Any) -> DailyBar:
    cierre = _decimal(close)
    if not cierre.is_finite() or cierre <= 0:
        raise SymbolError("cierre_invalido")
    volumen = None if volume is None else _decimal(volume)
    return DailyBar(ticker=ticker, fecha=day, cierre=cierre, volumen=volumen)


class _HttpProvider:
    """Pedidos por especie con concurrencia acotada y reintentos ante fallas transitorias."""

    name: str

    def __init__(self, http: httpx2.AsyncClient, *, retry_delay_seconds: float = 1.0) -> None:
        self._http = http
        self._retry_delay = retry_delay_seconds

    async def _get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._http.get(url, params=params)
            except httpx2.TransportError:
                reason = "sin_conexion"
            else:
                if response.status_code == httpx2.codes.OK:
                    try:
                        # Decimal desde el texto: sin errores de redondeo de float.
                        return json.loads(response.text, parse_float=Decimal, parse_int=Decimal)
                    except json.JSONDecodeError:
                        raise SymbolError("respuesta_no_json") from None
                reason = f"http_{response.status_code}"
                if response.status_code not in RETRYABLE_STATUS:
                    raise SymbolError(reason)
            if attempt < MAX_ATTEMPTS:
                await anyio.sleep(self._retry_delay * attempt)
        raise SymbolError(reason)

    async def _collect(
        self,
        symbols: Sequence[Symbol],
        fetch: Callable[[Symbol], Awaitable[list[DailyBar]]],
        start: date,
        end: date,
    ) -> FetchResult:
        result = FetchResult()
        limiter = anyio.Semaphore(MAX_CONCURRENT_REQUESTS)

        async def one(symbol: Symbol) -> None:
            async with limiter:
                try:
                    bars = await fetch(symbol)
                except SymbolError as exc:
                    result.errors[symbol.ticker] = exc.reason
                    logger.warning(
                        "precio_no_disponible",
                        proveedor=self.name,
                        ticker=symbol.ticker,
                        motivo=exc.reason,
                    )
                    return
            in_range = sorted((b for b in bars if start <= b.fecha <= end), key=lambda b: b.fecha)
            result.bars[symbol.ticker] = in_range

        async with anyio.create_task_group() as group:
            for symbol in symbols:
                group.start_soon(one, symbol)
        return result


class BymaOpenData(_HttpProvider):
    """Datos oficiales y gratuitos de BYMA: serie histórica diaria por especie (plazo 24 hs)."""

    name = "byma"
    SETTLEMENT = "24HS"

    def __init__(
        self, settings: Settings, http: httpx2.AsyncClient, *, retry_delay_seconds: float = 1.0
    ) -> None:
        super().__init__(http, retry_delay_seconds=retry_delay_seconds)
        base = str(settings.byma_open_data_base_url).rstrip("/")
        self._history_url = f"{base}/chart/historical-series/history"

    async def daily_bars(self, symbols: Sequence[Symbol], start: date, end: date) -> FetchResult:
        # La serie marca cada rueda con la medianoche de Buenos Aires (03:00 UTC del mismo día).
        since = int(datetime.combine(start, time(), tzinfo=UTC).timestamp())
        until = int(datetime.combine(end + timedelta(days=1), time(), tzinfo=UTC).timestamp())

        async def fetch(symbol: Symbol) -> list[DailyBar]:
            data = await self._get_json(
                self._history_url,
                {
                    "symbol": f"{symbol.ticker} {self.SETTLEMENT}",
                    "resolution": "D",
                    "from": str(since),
                    "to": str(until),
                },
            )
            if not isinstance(data, dict):
                raise SymbolError("formato_inesperado")
            if data.get("s") == "no_data":
                return []
            times, closes, volumes = data.get("t"), data.get("c"), data.get("v")
            if (
                data.get("s") != "ok"
                or not isinstance(times, list)
                or not isinstance(closes, list)
                or not isinstance(volumes, list)
                or not len(times) == len(closes) == len(volumes)
            ):
                raise SymbolError("formato_inesperado")
            try:
                days = [datetime.fromtimestamp(int(t), UTC).date() for t in times]
            except (TypeError, ValueError, OverflowError, OSError):
                raise SymbolError("formato_inesperado") from None
            return [
                _bar(symbol.ticker, day, close, volume)
                for day, close, volume in zip(days, closes, volumes, strict=True)
            ]

        return await self._collect(symbols, fetch, start, end)


class Data912(_HttpProvider):
    """Respaldo no oficial (data912.com): historia diaria completa por especie."""

    name = "data912"

    def __init__(
        self, settings: Settings, http: httpx2.AsyncClient, *, retry_delay_seconds: float = 1.0
    ) -> None:
        super().__init__(http, retry_delay_seconds=retry_delay_seconds)
        self._base = str(settings.data912_base_url).rstrip("/")

    async def daily_bars(self, symbols: Sequence[Symbol], start: date, end: date) -> FetchResult:
        async def fetch(symbol: Symbol) -> list[DailyBar]:
            path = DATA912_PATHS[symbol.kind]
            data = await self._get_json(
                f"{self._base}/historical/{path}/{quote(symbol.ticker, safe='')}"
            )
            if isinstance(data, dict) and "Error" in data:
                raise SymbolError("ticker_desconocido")
            if not isinstance(data, list):
                raise SymbolError("formato_inesperado")
            bars = []
            for row in data:
                try:
                    day = date.fromisoformat(row["date"])
                    close, volume = row["c"], row.get("v")
                except (KeyError, TypeError, ValueError):
                    raise SymbolError("formato_inesperado") from None
                if start <= day <= end:
                    bars.append(_bar(symbol.ticker, day, close, volume))
            return bars

        return await self._collect(symbols, fetch, start, end)
