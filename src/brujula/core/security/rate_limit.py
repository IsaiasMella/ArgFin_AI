"""Límite de pedidos por clave (IP o usuario) con ventana deslizante, en memoria.

Alcanza para el MVP: la API corre en un solo proceso. Con varias réplicas, cada una
contaría por separado y habría que mover el contador a PostgreSQL (ver docs/adr/007).
"""

import time
from collections import deque
from collections.abc import Callable

# Por encima de esta cantidad de claves se purgan las inactivas (acota la memoria).
MAX_TRACKED_KEYS = 10_000


class RateLimiter:
    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        """Registra un pedido de `key` y dice si está dentro del límite."""
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        if len(self._hits) > MAX_TRACKED_KEYS:
            self._purge(now)
        return True

    def _purge(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= self._window]:
            del self._hits[key]
