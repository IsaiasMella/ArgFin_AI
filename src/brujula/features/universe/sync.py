"""Sincroniza la base con `config/universe.yaml` (T2.1, ADR 010).

Idempotente: correrla dos veces seguidas no cambia nada la segunda vez. Lo que sale del archivo
queda inactivo en lugar de borrarse, porque puede haber posiciones que lo referencian.
"""

from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from brujula.features.universe.catalog import Universe
from brujula.features.universe.models import Company, Instrument


@dataclass(frozen=True)
class SyncResult:
    empresas_nuevas: int = 0
    empresas_actualizadas: int = 0
    empresas_desactivadas: int = 0
    instrumentos_nuevos: int = 0
    instrumentos_actualizados: int = 0
    instrumentos_desactivados: int = 0

    @property
    def changed(self) -> bool:
        return any(vars(self).values())


def _apply(row: Company | Instrument, values: dict[str, Any]) -> bool:
    """Copia los valores que difieren. Devuelve si cambió algo."""
    changed = False
    for name, value in values.items():
        if getattr(row, name) != value:
            setattr(row, name, value)
            changed = True
    return changed


def sync_universe(session: Session, universe: Universe) -> SyncResult:
    companies = {company.clave: company for company in session.scalars(select(Company))}
    instruments = {item.ticker_byma: item for item in session.scalars(select(Instrument))}
    counts: Counter[str] = Counter()
    listed_companies: set[str] = set()
    listed_tickers: set[str] = set()

    for entry in universe.empresas:
        listed_companies.add(entry.clave)
        company_values = {
            "nombre": entry.nombre,
            "sector": entry.sector,
            "pais": entry.pais,
            "tipo": entry.tipo,
            "cik_sec": entry.cik_sec,
            "url_relacion_inversores": entry.url_relacion_inversores,
            "activa": True,
        }
        company = companies.get(entry.clave)
        if company is None:
            company = Company(clave=entry.clave, **company_values)
            session.add(company)
            session.flush()  # obtiene el id para los instrumentos
            counts["empresas_nuevas"] += 1
        elif _apply(company, company_values):
            counts["empresas_actualizadas"] += 1

        for item in entry.instrumentos:
            listed_tickers.add(item.ticker_byma)
            instrument_values = {
                "company_id": company.id,
                "ticker_origen": item.ticker_origen,
                "ratio_cedear": item.ratio_cedear,
                "moneda": item.moneda,
                "activo": True,
            }
            instrument = instruments.get(item.ticker_byma)
            if instrument is None:
                session.add(Instrument(ticker_byma=item.ticker_byma, **instrument_values))
                counts["instrumentos_nuevos"] += 1
            elif _apply(instrument, instrument_values):
                counts["instrumentos_actualizados"] += 1

    for clave, company in companies.items():
        if clave not in listed_companies and company.activa:
            company.activa = False
            counts["empresas_desactivadas"] += 1
    for ticker, instrument in instruments.items():
        if ticker not in listed_tickers and instrument.activo:
            instrument.activo = False
            counts["instrumentos_desactivados"] += 1

    session.flush()
    return SyncResult(**counts)
