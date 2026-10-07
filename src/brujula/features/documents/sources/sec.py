"""Formularios 6-K de la SEC (comunicados de las empresas argentinas con ADR, ADR 013).

La SEC exige un User-Agent con un email de contacto (`SEC_USER_AGENT`) y admite hasta 10
pedidos por segundo: se va de a uno.
"""

from dataclasses import dataclass
from datetime import date

import httpx2

from brujula.core.config import Settings
from brujula.core.http import FetchError, fetch


@dataclass(frozen=True)
class Filing:
    accession: str  # con guiones, p. ej. 0001292814-26-004047
    form: str
    filing_date: date
    primary_document: str


class SecClient:
    def __init__(
        self, settings: Settings, http: httpx2.AsyncClient, *, retry_delay_seconds: float = 1.0
    ) -> None:
        self._http = http
        self._data = str(settings.sec_data_base_url).rstrip("/")
        self._archives = str(settings.sec_archives_base_url).rstrip("/")
        self._headers = {"User-Agent": settings.sec_user_agent}
        self._delay = retry_delay_seconds

    async def _get(self, url: str) -> httpx2.Response:
        return await fetch(
            self._http, "GET", url, headers=self._headers, retry_delay_seconds=self._delay
        )

    async def filings(self, cik: str, form: str, since: date) -> list[Filing]:
        """Presentaciones recientes del formulario pedido (la SEC lista las últimas ~1000)."""
        data = (await self._get(f"{self._data}/submissions/CIK{cik}.json")).json()
        try:
            recent = data["filings"]["recent"]
            rows = zip(
                recent["accessionNumber"],
                recent["form"],
                recent["filingDate"],
                recent["primaryDocument"],
                strict=True,
            )
            return [
                Filing(accession=a, form=f, filing_date=date.fromisoformat(d), primary_document=p)
                for a, f, d, p in rows
                if f == form and date.fromisoformat(d) >= since
            ]
        except (KeyError, TypeError, ValueError):
            raise FetchError("sec_formato_inesperado") from None

    def _folder(self, cik: str, accession: str) -> str:
        return f"{self._archives}/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"

    def file_url(self, cik: str, accession: str, name: str) -> str:
        return f"{self._folder(cik, accession)}/{name}"

    async def files(self, cik: str, accession: str) -> list[str]:
        data = (await self._get(f"{self._folder(cik, accession)}/index.json")).json()
        try:
            return [item["name"] for item in data["directory"]["item"]]
        except (KeyError, TypeError):
            raise FetchError("sec_formato_inesperado") from None

    async def download(self, cik: str, accession: str, name: str) -> bytes:
        return (await self._get(self.file_url(cik, accession, name))).content
