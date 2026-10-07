"""Autopista de Información Financiera de la CNV (ADR 013).

- Listado: la ficha de la empresa (`Empresas/Empresa/<cuit>`) con estados contables
  (`formType=INFOFI`) o hechos relevantes (`formType=HECHOR`).
- Presentación: la vista pública del AIF trae un XML con metadatos, el plan de cuentas
  (estados contables) y los adjuntos (nombre y `guid`).
- Descarga: una clave temporal (`GetPublicValetKey`) y un POST al servicio de blobs.
"""

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

import httpx2
from defusedxml import ElementTree

from brujula.core.config import Settings
from brujula.core.http import FetchError, fetch

MONTHS = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "set": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}
# "NORMA CONTABLE: NIIF - TIPO BALANCE: CONSOLIDADO - PERIODICIDAD: 3 - FECHA CIERRE: 2026-06-30"
_OWN_STATEMENT = re.compile(
    r"TIPO BALANCE:\s*(CONSOLIDADO|INDIVIDUAL)\s*-\s*PERIODICIDAD:\s*(\d)\s*-\s*"
    r"FECHA CIERRE:\s*(\d{4}-\d{2}-\d{2})"
)
_PRESENTATION_XML = re.compile(r"var presentation\s*=\s*'(.*?)';\s*$", re.MULTILINE | re.DOTALL)
PERIODICITY = {"3": "trimestral", "1": "anual"}

FormType = Literal["INFOFI", "HECHOR"]


class CnvFormatError(Exception):
    """La CNV respondió con un formato distinto del esperado (posible cambio del sitio)."""


@dataclass(frozen=True)
class Listing:
    fecha: date | None
    descripcion: str
    presentacion_id: int
    view_url: str


@dataclass(frozen=True)
class StatementRef:
    listing: Listing
    tipo_balance: Literal["consolidado", "individual"]
    periodicidad: str
    fecha_cierre: date


@dataclass(frozen=True)
class Attachment:
    propiedad: str
    nombre: str
    guid: str


@dataclass
class Presentation:
    propiedades: dict[str, str] = field(default_factory=dict)
    cuentas: list[dict[str, str]] = field(default_factory=list)
    adjuntos: list[Attachment] = field(default_factory=list)


def parse_spanish_date(text: str) -> date | None:
    match = re.match(r"\s*(\d{1,2})\s+([a-záéíóú]{3})\w*\.?\s+(\d{4})", text.lower())
    if not match or match.group(2) not in MONTHS:
        return None
    return date(int(match.group(3)), MONTHS[match.group(2)], int(match.group(1)))


def _cells(row: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
        for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
    ]


def parse_listing(page: str, view_base: str) -> list[Listing]:
    """Filas de la ficha que apuntan a una presentación del AIF."""
    listings = []
    for row in re.findall(r"<tr>(.*?)</tr>", page, re.DOTALL):
        views = [
            html.unescape(link)
            for link in re.findall(r'href="([^"]+)"', row)
            if link.startswith(f"{view_base}/presentations/publicview/")
        ]
        cells = _cells(row)
        if not views or len(cells) < 4 or not cells[3].isdigit():
            continue
        listings.append(
            Listing(
                fecha=parse_spanish_date(cells[0]),
                descripcion=cells[2],
                presentacion_id=int(cells[3]),
                view_url=views[0],
            )
        )
    return listings


def own_statement(listing: Listing) -> StatementRef | None:
    """Estado contable propio (no de una controlada) con período reconocible."""
    if listing.descripcion.startswith("RELAC."):
        return None
    match = _OWN_STATEMENT.search(listing.descripcion)
    if not match or match.group(2) not in PERIODICITY:
        return None
    return StatementRef(
        listing=listing,
        tipo_balance="consolidado" if match.group(1) == "CONSOLIDADO" else "individual",
        periodicidad=PERIODICITY[match.group(2)],
        fecha_cierre=date.fromisoformat(match.group(3)),
    )


def parse_presentation(page: str) -> Presentation:
    match = _PRESENTATION_XML.search(page)
    if not match:
        raise CnvFormatError("presentacion_sin_xml")
    raw = match.group(1).replace("\\'", "'").replace("\\\\", "\\")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        raise CnvFormatError("presentacion_xml_invalido") from None

    presentation = Presentation()
    for entity in root.iter("entidad"):
        if entity.get("clave") == "Estados Contables Cuentas":
            for row in entity.iter("fila"):
                values = {p.get("id", ""): (p.text or "").strip() for p in row.iter("propiedad")}
                presentation.cuentas.append(
                    {
                        "nro": values.get("Nro", ""),
                        "rubro": values.get("Rubro", ""),
                        "monto": values.get("Monto", ""),
                    }
                )
            continue
        for prop in entity.findall("propiedad"):
            prop_id, text = prop.get("id", ""), (prop.text or "").strip()
            if prop.get("uploader") is not None:
                try:
                    files = json.loads(text) if text else []
                except json.JSONDecodeError:
                    raise CnvFormatError("adjuntos_invalidos") from None
                presentation.adjuntos.extend(
                    Attachment(propiedad=prop_id, nombre=f["nombreArchivo"], guid=f["guid"])
                    for f in files
                    if f.get("guid") and f.get("nombreArchivo")
                )
            elif text:
                presentation.propiedades[prop_id] = text
    return presentation


def presentation_date(value: str | None) -> date | None:
    """`2026-06-30T03:00:00.000Z` es la medianoche de Buenos Aires: la fecha es la parte UTC."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


class CnvClient:
    def __init__(
        self, settings: Settings, http: httpx2.AsyncClient, *, retry_delay_seconds: float = 1.0
    ) -> None:
        self._http = http
        self._site = str(settings.cnv_base_url).rstrip("/")
        self._aif = str(settings.cnv_aif_base_url).rstrip("/")
        self._blob = str(settings.cnv_blob_base_url).rstrip("/")
        self._delay = retry_delay_seconds

    async def _get(self, url: str, params: dict[str, str] | None = None) -> httpx2.Response:
        return await fetch(self._http, "GET", url, params=params, retry_delay_seconds=self._delay)

    async def listings(self, cuit: str, form_type: FormType, since: date) -> list[Listing]:
        response = await self._get(
            f"{self._site}/Empresas/Empresa/{cuit}",
            params={"formType": form_type, "fdesde": f"{since.day}/{since.month}/{since.year}"},
        )
        return parse_listing(response.text, self._aif)

    async def presentation(self, listing: Listing) -> Presentation:
        return parse_presentation((await self._get(listing.view_url)).text)

    async def download(self, guid: str) -> bytes:
        key_response = await self._get(
            f"{self._aif}/api/ValetKeyProvider/GetPublicValetKey/{guid}",
            params={"operation": "DownloadBlob"},
        )
        try:
            valet_key = key_response.json()["valetKeyData"]
        except (ValueError, KeyError, TypeError):
            raise FetchError("clave_de_descarga_invalida") from None
        response = await fetch(
            self._http,
            "POST",
            f"{self._blob}/DownloadBlob/{guid}",
            data={"ValetKey": valet_key},
            retry_delay_seconds=self._delay,
        )
        return response.content
