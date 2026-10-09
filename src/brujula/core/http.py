"""Pedidos HTTP salientes a fuentes externas, con reintentos ante fallas transitorias."""

from typing import Any

import anyio
import httpx2

MAX_ATTEMPTS = 3
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class FetchError(Exception):
    """El pedido no se pudo completar. `reason` es corto y sin datos sensibles."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FormatError(FetchError):
    """La fuente respondió, pero con un formato distinto del esperado (posible cambio del sitio).

    El monitor de integraciones (T3.7) lo avisa enseguida: no se arregla reintentando.
    """


async def fetch(
    http: httpx2.AsyncClient,
    method: str,
    url: str,
    *,
    retry_delay_seconds: float = 1.0,
    **kwargs: Any,
) -> httpx2.Response:
    """Devuelve la respuesta 200. Reintenta red caída, 429 y 5xx; un 4xx falla enseguida."""
    reason = "sin_respuesta"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await http.request(method, url, **kwargs)
        except httpx2.TransportError:
            reason = "sin_conexion"
        else:
            if response.status_code == httpx2.codes.OK:
                return response
            reason = f"http_{response.status_code}"
            if response.status_code not in RETRYABLE_STATUS:
                raise FetchError(reason)
        if attempt < MAX_ATTEMPTS:
            await anyio.sleep(retry_delay_seconds * attempt)
    raise FetchError(reason)
