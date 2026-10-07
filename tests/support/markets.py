"""BYMA Open Data y data912 simulados con respuestas reales grabadas (tests/fixtures/prices).

Grabadas el 2026-10-07: ruedas del 28/09 al 06/10/2026. Sin llamadas reales en los tests.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

import httpx2

from tests.support.env import VALID_ENV

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prices"
BYMA_BASE = VALID_ENV["BYMA_OPEN_DATA_BASE_URL"]
DATA912_BASE = VALID_ENV["DATA912_BASE_URL"]
HISTORY_PATH = "/chart/historical-series/history"
# Así responden las fuentes reales a un ticker que no tienen.
UNKNOWN = {"byma": "byma_NOEXISTE.json", "data912": "data912_stocks_NOEXISTE.json"}


@dataclass
class RecordedMarkets:
    # Respuestas forzadas por ticker (en orden; la última se repite): status o cuerpo crudo.
    scripted: dict[tuple[str, str], list[httpx2.Response]] = field(default_factory=dict)
    # Cierres reemplazados en las respuestas: (ticker, fecha ISO) -> cierre.
    data912_closes: dict[tuple[str, str], float] = field(default_factory=dict)
    byma_closes: dict[tuple[str, str], float] = field(default_factory=dict)
    requests: Counter[tuple[str, str]] = field(default_factory=Counter)
    byma_params: list[dict[str, str]] = field(default_factory=list)

    def script(self, provider: str, ticker: str, *responses: httpx2.Response) -> None:
        self.scripted[(provider, ticker)] = list(responses)

    def _scripted(self, provider: str, ticker: str) -> httpx2.Response | None:
        queue = self.scripted.get((provider, ticker))
        if not queue:
            return None
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url).split("?")[0]
        if url == BYMA_BASE + HISTORY_PATH:
            params = dict(request.url.params)
            self.byma_params.append(params)
            ticker = params["symbol"].removesuffix(" 24HS")
            response = self._respond("byma", ticker, FIXTURES / f"byma_{ticker}.json")
            return self._patch_byma(ticker, response)
        if url.startswith(DATA912_BASE + "/historical/"):
            kind, ticker = url.removeprefix(DATA912_BASE + "/historical/").split("/")
            ticker = unquote(ticker)
            response = self._respond("data912", ticker, FIXTURES / f"data912_{kind}_{ticker}.json")
            return self._patch_data912(ticker, response)
        return httpx2.Response(404)

    def _respond(self, provider: str, ticker: str, path: Path) -> httpx2.Response:
        self.requests[(provider, ticker)] += 1
        scripted = self._scripted(provider, ticker)
        if scripted is not None:
            return scripted
        if not path.exists():
            return httpx2.Response(200, content=(FIXTURES / UNKNOWN[provider]).read_bytes())
        return httpx2.Response(200, content=path.read_bytes())

    def _patch_byma(self, ticker: str, response: httpx2.Response) -> httpx2.Response:
        if response.status_code != 200 or not any(t == ticker for t, _ in self.byma_closes):
            return response
        data = response.json()
        for i, timestamp in enumerate(data["t"]):
            day = datetime.fromtimestamp(timestamp, UTC).date().isoformat()
            close = self.byma_closes.get((ticker, day))
            if close is not None:
                data["c"][i] = close
        return httpx2.Response(200, json=data)

    def _patch_data912(self, ticker: str, response: httpx2.Response) -> httpx2.Response:
        if response.status_code != 200 or not any(t == ticker for t, _ in self.data912_closes):
            return response
        rows = response.json()
        for row in rows:
            close = self.data912_closes.get((ticker, row["date"]))
            if close is not None:
                row["c"] = close
        return httpx2.Response(200, json=rows)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handler)
