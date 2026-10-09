"""Envío de emails por Resend (por ahora, solo los avisos internos del monitor, T3.7).

El envío a usuarios (plantillas, baja en un clic, preferencias) es T6.1.
"""

from collections.abc import Sequence
from typing import Protocol

import httpx2

from brujula.core.config import Settings
from brujula.core.http import fetch


class EmailSender(Protocol):
    async def send(
        self, *, to: Sequence[str], subject: str, text: str, idempotency_key: str
    ) -> None: ...


class ResendEmailSender:
    def __init__(self, settings: Settings, http: httpx2.AsyncClient) -> None:
        self._url = f"{str(settings.resend_api_base_url).rstrip('/')}/emails"
        self._key = settings.resend_api_key
        self._from = settings.email_from
        self._http = http

    async def send(
        self, *, to: Sequence[str], subject: str, text: str, idempotency_key: str
    ) -> None:
        """Lanza `FetchError` si no se pudo enviar.

        `idempotency_key` evita un email duplicado si un reintento llega después de un envío
        que sí se hizo (Resend lo respeta durante 24 horas).
        """
        await fetch(
            self._http,
            "POST",
            self._url,
            headers={
                "Authorization": f"Bearer {self._key.get_secret_value()}",
                "Idempotency-Key": idempotency_key,
            },
            json={"from": self._from, "to": list(to), "subject": subject, "text": text},
        )
