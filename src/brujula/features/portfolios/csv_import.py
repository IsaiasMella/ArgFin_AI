"""Lectura y validación estricta del CSV de portafolio (T1.4).

Errores de archivo (tamaño, codificación, columnas, cantidad de filas) rechazan todo.
Errores de fila se informan por fila y esas filas no se guardan; las válidas sí.

Acepta lo que suele exportar Excel en Argentina: separador `;`, coma decimal y archivos
en UTF-8 (con o sin BOM) o Windows-1252.
"""

import csv
import io
from dataclasses import dataclass, field

from pydantic import ValidationError

from brujula.features.portfolios.schemas import HoldingCreate

REQUIRED_COLUMNS = ("ticker", "cantidad")
OPTIONAL_COLUMNS = ("precio_promedio", "moneda_precio", "broker")
COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS
NUMERIC_COLUMNS = ("cantidad", "precio_promedio")
TEMPLATE = ",".join(COLUMNS) + "\r\n"

# Mensajes en castellano para los errores de validación más comunes.
FRIENDLY_ERRORS = {
    "missing": "es obligatorio",
    "greater_than": "debe ser mayor que 0",
    "decimal_parsing": "no es un número",
    "decimal_max_digits": "tiene demasiados dígitos",
    "decimal_max_places": "tiene demasiados decimales",
    "literal_error": "debe ser ARS o USD",
    "string_too_long": "es demasiado largo",
}


class CsvFormatError(ValueError):
    """El archivo completo es inválido; el mensaje es para el usuario."""


@dataclass(frozen=True)
class RowError:
    row: int
    errors: list[str]


@dataclass
class ParsedCsv:
    valid: list[tuple[int, HoldingCreate]] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    rows: int = 0


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CsvFormatError("El archivo no es texto (UTF-8 o Windows-1252)")


def _normalize_number(value: str) -> str:
    value = value.strip()
    if "," in value and "." in value:
        raise ValueError("formato ambiguo: usá coma o punto decimal, sin separador de miles")
    return value.replace(",", ".")


def _friendly(exc: ValidationError) -> list[str]:
    messages = []
    for error in exc.errors(include_input=False, include_url=False):
        column = ".".join(str(part) for part in error["loc"]) or "fila"
        if error["type"] == "value_error":
            message = str(error["ctx"]["error"]) if "ctx" in error else error["msg"]
        else:
            message = FRIENDLY_ERRORS.get(error["type"], error["msg"])
        messages.append(f"{column}: {message}")
    return messages


def parse_csv(content: bytes, *, max_bytes: int, max_rows: int) -> ParsedCsv:
    if not content.strip():
        raise CsvFormatError("El archivo está vacío")
    if len(content) > max_bytes:
        raise CsvFormatError(f"El archivo supera el máximo de {max_bytes} bytes")
    text = _decode(content)
    first_line = text.splitlines()[0]
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)

    try:
        header = [column.strip().lower() for column in next(reader)]
        missing = [column for column in REQUIRED_COLUMNS if column not in header]
        unknown = [column for column in header if column not in COLUMNS]
        if missing or unknown or len(set(header)) != len(header):
            raise CsvFormatError(
                "Columnas inválidas. Se esperan: "
                + ", ".join(COLUMNS)
                + (f" (faltan: {', '.join(missing)})" if missing else "")
                + (f" (sobran: {', '.join(unknown)})" if unknown else "")
            )
        result = ParsedCsv()
        for line_number, values in enumerate(reader, start=2):
            if not any(value.strip() for value in values):
                continue  # filas en blanco: se ignoran
            result.rows += 1
            if result.rows > max_rows:
                raise CsvFormatError(f"El archivo supera el máximo de {max_rows} filas")
            if len(values) != len(header):
                result.errors.append(
                    RowError(
                        line_number, [f"tiene {len(values)} columnas y se esperan {len(header)}"]
                    )
                )
                continue
            _parse_row(result, line_number, dict(zip(header, values, strict=True)))
    except csv.Error as exc:
        raise CsvFormatError(f"CSV mal formado: {exc}") from None
    return result


def _parse_row(result: ParsedCsv, line_number: int, raw: dict[str, str]) -> None:
    row: dict[str, str | None] = {key: value.strip() or None for key, value in raw.items()}
    for column in NUMERIC_COLUMNS:
        value = row.get(column)
        if value is not None:
            try:
                row[column] = _normalize_number(value)
            except ValueError as exc:
                result.errors.append(RowError(line_number, [f"{column}: {exc}"]))
                return
    if row.get("moneda_precio"):
        row["moneda_precio"] = (row["moneda_precio"] or "").upper()
    try:
        holding = HoldingCreate.model_validate(
            {key: value for key, value in row.items() if value is not None}
        )
    except ValidationError as exc:
        result.errors.append(RowError(line_number, _friendly(exc)))
        return
    result.valid.append((line_number, holding))
