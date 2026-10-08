"""Texto de documentos (HTML y PDF) e inferencia del período al que se refieren."""

import calendar
import html
import re
from datetime import date

import pymupdf

QUARTER_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "primer": 1,
    "segundo": 2,
    "tercer": 3,
    "cuarto": 4,
}
# El año va con 2 o 4 dígitos; el separador puede ser espacio, guion o guion bajo.
_QUARTER_FIRST = re.compile(r"(?<!\d)([1-4])\s?(?:[QqTt]|[°º]\s?[Tt])[\s_\-]?(20\d{2}|\d{2})(?!\d)")
_YEAR_FIRST = re.compile(r"(?<!\d)(20\d{2})[\s_\-]?[QqTt]([1-4])(?!\d)")
_WORDS = re.compile(
    r"(first|second|third|fourth|primer|segundo|tercer|cuarto)\s+(?:quarter|trimestre)"
    r"(?:\s+(?:of|de|del))?(?:\s+(?:fiscal\s+)?(?:year\s+)?(?:ejercicio\s+)?)?\s*(20\d{2})",
    re.IGNORECASE,
)
_ORDINAL = re.compile(
    r"(?<!\d)([1-4])\s?[°ºo]?\s*(?:trimestre|quarter)(?:\s+(?:de|del|of))?\s*(20\d{2})",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)


def quarter_end(year: int, quarter: int) -> date:
    month = quarter * 3
    return date(year, month, calendar.monthrange(year, month)[1])


def _year(text: str) -> int:
    value = int(text)
    return value + 2000 if value < 100 else value


def infer_period(text: str) -> date | None:
    """Fin del trimestre mencionado (p. ej. "2Q26", "IR-2026Q2", "segundo trimestre 2026")."""
    if match := _WORDS.search(text):
        return quarter_end(int(match.group(2)), QUARTER_WORDS[match.group(1).lower()])
    if match := _ORDINAL.search(text):
        return quarter_end(int(match.group(2)), int(match.group(1)))
    if match := _YEAR_FIRST.search(text):
        return quarter_end(int(match.group(1)), int(match.group(2)))
    if match := _QUARTER_FIRST.search(text):
        return quarter_end(_year(match.group(2)), int(match.group(1)))
    return None


def html_text(content: bytes) -> str:
    raw = content.decode("utf-8", errors="replace")
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", raw))).strip()


def pdf_pages(content: bytes) -> list[str]:
    """Texto de cada página de un PDF (vacío si la página es una imagen escaneada)."""
    # PyMuPDF no tiene anotaciones de tipos.
    with pymupdf.open(stream=content, filetype="pdf") as document:  # type: ignore[no-untyped-call]
        return [page.get_text() for page in document]


def ocr_pages(content: bytes, numbers: list[int], *, language: str, dpi: int) -> dict[int, str]:
    """Texto por OCR (Tesseract vía PyMuPDF) de las páginas pedidas (1-based).

    Si Tesseract no está instalado o una página falla, esa página no se devuelve: quien llama
    la trata como página sin texto (no se adivina nada).
    """
    texts: dict[int, str] = {}
    with pymupdf.open(stream=content, filetype="pdf") as document:  # type: ignore[no-untyped-call]
        for number in numbers:
            page = document[number - 1]
            try:
                textpage = page.get_textpage_ocr(language=language, dpi=dpi, full=True)
            except RuntimeError:
                continue
            texts[number] = page.get_text(textpage=textpage)
    return texts


def document_text(content: bytes, filename: str, max_chars: int | None = None) -> str:
    if filename.lower().endswith(".pdf"):
        text = re.sub(r"\s+", " ", " ".join(pdf_pages(content))).strip()
    else:
        text = html_text(content)
    return text if max_chars is None else text[:max_chars]
