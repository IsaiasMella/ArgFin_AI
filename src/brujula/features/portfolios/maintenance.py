"""Mantenimiento de posiciones que corre como operación (CLI), con el rol de migraciones."""

from sqlalchemy import text
from sqlalchemy.orm import Session


def link_free_tickers(session: Session) -> int:
    """Vincula al universo las posiciones cargadas como ticker libre que ahora están cubiertas.

    Pasa cuando el universo suma un ticker que algún usuario ya tenía (ADR 010). Devuelve
    cuántas posiciones vinculó.
    """
    result = session.execute(
        text(
            "UPDATE holdings SET instrument_id = i.id, ticker_libre = NULL"
            " FROM instruments i WHERE holdings.ticker_libre = i.ticker_byma"
        )
    )
    return int(getattr(result, "rowcount", 0))
