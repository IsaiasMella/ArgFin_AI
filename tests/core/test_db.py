"""Utilidades de `core/db.py` que no necesitan una base real."""

from brujula.core.db import libpq_conninfo


def test_libpq_conninfo_quita_el_driver_y_conserva_la_clave() -> None:
    url = "postgresql+psycopg://app:cl%40ve@db:5432/brujula"

    assert libpq_conninfo(url) == "postgresql://app:cl%40ve@db:5432/brujula"
