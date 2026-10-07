"""Tickers BYMA: un único criterio de validación para el universo y los portafolios."""

import re
from typing import Annotated

from pydantic import AfterValidator

# Letras y números, a veces con punto o guion (p. ej. "BRK.B").
TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")


def normalize_ticker(value: str) -> str:
    ticker = value.strip().upper()
    if not TICKER_PATTERN.match(ticker):
        raise ValueError("ticker inválido: letras, números, punto o guion (hasta 12)")
    return ticker


Ticker = Annotated[str, AfterValidator(normalize_ticker)]
