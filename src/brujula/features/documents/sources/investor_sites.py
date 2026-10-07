"""Sitios de relación con inversores (ADR 013). Dos lectores genéricos, configurados por
empresa en `universe.yaml` (bloque `inversores`):

- `enlaces_pdf`: los PDF enlazados en una página.
- `wordpress_media`: la API de medios de WordPress del sitio (`/wp-json/wp/v2/media`).

En ambos casos, el comunicado se reconoce por un patrón sobre el nombre del archivo.
"""

import html
import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import unquote, urljoin, urlsplit

import httpx2

from brujula.core.http import FetchError, fetch
from brujula.features.universe.catalog import InvestorSite

# Identificación honesta del sistema frente a los sitios.
USER_AGENT = "Mozilla/5.0 (compatible; Brujula/0.1; +seguimiento informativo de emisoras)"
WORDPRESS_PAGE_SIZE = 100


@dataclass(frozen=True)
class SiteDocument:
    url: str
    nombre: str
    fecha: date | None


def filename(url: str) -> str:
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def pdf_links(page: str, base_url: str, pattern: str) -> list[SiteDocument]:
    found: dict[str, SiteDocument] = {}
    for href in re.findall(r'href="([^"]+)"', page, re.IGNORECASE):
        url = urljoin(base_url, html.unescape(href).strip())
        name = filename(url)
        if name.lower().endswith(".pdf") and re.search(pattern, name):
            found.setdefault(url, SiteDocument(url=url, nombre=name, fecha=None))
    return list(found.values())


class InvestorSiteClient:
    def __init__(self, http: httpx2.AsyncClient, *, retry_delay_seconds: float = 1.0) -> None:
        self._http = http
        self._delay = retry_delay_seconds
        self._headers = {"User-Agent": USER_AGENT}

    async def _get(self, url: str, params: dict[str, str] | None = None) -> httpx2.Response:
        return await fetch(
            self._http,
            "GET",
            url,
            params=params,
            headers=self._headers,
            follow_redirects=True,
            retry_delay_seconds=self._delay,
        )

    async def documents(self, site: InvestorSite, since: date) -> list[SiteDocument]:
        if site.acceso == "enlaces_pdf":
            response = await self._get(site.url)
            return pdf_links(response.text, str(response.url), site.patron)
        return await self._wordpress(site, since)

    async def _wordpress(self, site: InvestorSite, since: date) -> list[SiteDocument]:
        response = await self._get(
            f"{site.url.rstrip('/')}/wp-json/wp/v2/media",
            params={
                "mime_type": "application/pdf",
                "after": f"{since.isoformat()}T00:00:00",
                "per_page": str(WORDPRESS_PAGE_SIZE),
                "orderby": "date",
                "order": "desc",
            },
        )
        try:
            items = response.json()
            documents = []
            for item in items:
                url = item["source_url"]
                name = filename(url)
                if re.search(site.patron, name):
                    published = datetime.fromisoformat(item["date"]).date()
                    documents.append(SiteDocument(url=url, nombre=name, fecha=published))
        except (ValueError, KeyError, TypeError):
            raise FetchError("wordpress_formato_inesperado") from None
        return documents

    async def download(self, url: str) -> bytes:
        return (await self._get(url)).content
