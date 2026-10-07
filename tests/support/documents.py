"""CNV, SEC y sitios de inversores simulados para los tests de la ingesta (T3.2).

Las páginas de la CNV son recortes de respuestas reales (tests/fixtures/documents). Los PDF
se generan en el momento, uno distinto por archivo, para que cada uno tenga su hash.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs

import httpx2
import pymupdf

from tests.support.env import VALID_ENV

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "documents"
CNV = VALID_ENV["CNV_BASE_URL"]
AIF = VALID_ENV["CNV_AIF_BASE_URL"]
BLOB = VALID_ENV["CNV_BLOB_BASE_URL"]
SEC_DATA = VALID_ENV["SEC_DATA_BASE_URL"]
SEC_ARCHIVES = VALID_ENV["SEC_ARCHIVES_BASE_URL"]
REAL_AIF = "https://aif2.cnv.gov.ar"

GGAL_CUIT = "30704962807"
GGAL_CIK = "0001114700"
# La presentación del hecho relevante de resultados en el listado grabado.
RESULTS_FACT_VIEW = "1190290b-122a-4c6b-89d3-da37daf96897"
SITE = "https://inversores.example.invalid"
WORDPRESS = "https://wp.example.invalid"

RESULTS_6K = "0001114700-26-000101"
OTHER_6K = "0001114700-26-000102"


def pdf(text: str) -> bytes:
    document = pymupdf.open()  # type: ignore[no-untyped-call]
    page = document.new_page()
    page.insert_text((72, 72), text)
    content: bytes = document.tobytes()  # type: ignore[no-untyped-call]
    return content


def _fixture(name: str) -> str:
    # Los links grabados apuntan al AIF real: se reescriben al de los tests.
    return (FIXTURES / name).read_text(encoding="utf-8").replace(REAL_AIF, AIF)


SUBMISSIONS = {
    "filings": {
        "recent": {
            "accessionNumber": [RESULTS_6K, OTHER_6K, "0001114700-25-000001"],
            "form": ["6-K", "6-K", "6-K"],
            "filingDate": ["2026-08-25", "2026-09-02", "2025-01-10"],
            "primaryDocument": ["ggal6k.htm", "otro6k.htm", "viejo6k.htm"],
        }
    }
}
SEC_FILES = {
    RESULTS_6K: {
        "ggal6k.htm": "<html><body>FORM 6-K. Grupo Financiero Galicia</body></html>",
        "pressrelease.htm": (
            "<html><body><p>Grupo Financiero Galicia S.A. reports results for the second"
            " quarter of 2026.</p></body></html>"
        ),
        "logo.jpg": "binario",
    },
    OTHER_6K: {
        "otro6k.htm": "<html><body>Notice of a shareholders meeting.</body></html>",
    },
}

SITE_PAGE = """
<html><body>
<a href="/docs/informe_de_resultados_2q_2026.pdf">2T26</a>
<a href="/docs/informe_de_resultados_1q_2026.pdf">1T26</a>
<a href="/docs/informe_de_resultados_4q_2022.pdf">viejo</a>
<a href="/docs/EEFF-30-06-2026.pdf">estados</a>
<a href="/docs/informe_de_resultados_2q_2026.pdf">repetido</a>
</body></html>
"""
WORDPRESS_MEDIA = [
    {
        "date": "2026-08-20T12:26:12",
        "source_url": f"{WORDPRESS}/wp-content/uploads/2026/08/IR-2026Q2-ESP-Press-Release.pdf",
    },
    {
        "date": "2026-08-25T14:41:30",
        "source_url": f"{WORDPRESS}/wp-content/uploads/2026/08/IR-2026Q2-ENG-Conference-Call.pdf",
    },
]


@dataclass
class FakeSources:
    # Respuesta forzada por fragmento de URL (para simular fallas).
    failures: dict[str, httpx2.Response] = field(default_factory=dict)
    # El XML de esta presentación se reemplaza por HTML sin XML (cambio de formato).
    broken_views: set[str] = field(default_factory=set)
    requests: Counter[str] = field(default_factory=Counter)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handler)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url).split("?")[0]
        for fragment, response in self.failures.items():
            if fragment in str(request.url):
                return response
        kind = self._route(request, url)
        self.requests[kind[0]] += 1
        return kind[1]

    def _route(self, request: httpx2.Request, url: str) -> tuple[str, httpx2.Response]:
        params = dict(request.url.params)
        if url == f"{CNV}/Empresas/Empresa/{GGAL_CUIT}":
            name = (
                "cnv_listado_estados.html"
                if params["formType"] == "INFOFI"
                else ("cnv_listado_hechos.html")
            )
            return "cnv_listado", httpx2.Response(200, text=_fixture(name))
        if url.startswith(f"{CNV}/Empresas/Empresa/"):
            return "cnv_listado", httpx2.Response(200, text="<html><table></table></html>")
        if url.startswith(f"{AIF}/presentations/publicview/"):
            view = url.rsplit("/", 1)[1]
            if view in self.broken_views:
                return "aif_vista", httpx2.Response(200, text="<html>mantenimiento</html>")
            is_fact = view == RESULTS_FACT_VIEW
            name = "aif_hecho_relevante.html" if is_fact else "aif_estado_contable.html"
            return "aif_vista", httpx2.Response(200, text=_fixture(name))
        if url.startswith(f"{AIF}/api/ValetKeyProvider/GetPublicValetKey/"):
            guid = url.rsplit("/", 1)[1]
            return "aif_clave", httpx2.Response(200, json={"valetKeyData": f"clave-{guid}"})
        if url.startswith(f"{BLOB}/DownloadBlob/"):
            guid = url.rsplit("/", 1)[1]
            sent = parse_qs(request.content.decode())
            if sent.get("ValetKey") != [f"clave-{guid}"]:
                return "blob", httpx2.Response(403)
            return "blob", httpx2.Response(200, content=pdf(f"Documento {guid}"))
        if url == f"{SEC_DATA}/submissions/CIK{GGAL_CIK}.json":
            return "sec_listado", httpx2.Response(200, json=SUBMISSIONS)
        match = re.match(rf"{re.escape(SEC_ARCHIVES)}/Archives/edgar/data/1114700/(\d+)/(.+)", url)
        if match:
            accession = next(a for a in SEC_FILES if a.replace("-", "") == match.group(1))
            files = SEC_FILES[accession]
            if match.group(2) == "index.json":
                items = [{"name": n} for n in [*files, f"{accession}-index.html"]]
                return "sec_indice", httpx2.Response(200, json={"directory": {"item": items}})
            return "sec_archivo", httpx2.Response(200, text=files[match.group(2)])
        if url == f"{SITE}/inversores":
            return "sitio", httpx2.Response(200, text=SITE_PAGE)
        if url.startswith(f"{SITE}/docs/") or url.startswith(f"{WORDPRESS}/wp-content/"):
            return "sitio_pdf", httpx2.Response(200, content=pdf(url))
        if url == f"{WORDPRESS}/wp-json/wp/v2/media":
            return "wordpress", httpx2.Response(200, content=json.dumps(WORDPRESS_MEDIA))
        return "desconocido", httpx2.Response(404)
