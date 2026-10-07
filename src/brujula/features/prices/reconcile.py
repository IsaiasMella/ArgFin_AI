"""Validación cruzada de dos fuentes de precios (plan 7.1). Funciones puras, sin E/S."""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from brujula.features.prices.providers import DailyBar

PCT_QUANTUM = Decimal("0.0001")
PRICE_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class Reconciled:
    valor: Decimal
    volumen: Decimal | None
    fuente: str
    valor_respaldo: Decimal | None
    divergencia_pct: Decimal | None
    marcado: bool


def divergence_pct(primary: Decimal, backup: Decimal) -> Decimal:
    """Diferencia del respaldo respecto del principal, en porcentaje absoluto."""
    return (abs(primary - backup) / primary * 100).quantize(PCT_QUANTUM, ROUND_HALF_EVEN)


def reconcile(
    primary: DailyBar | None,
    backup: DailyBar | None,
    *,
    primary_name: str,
    backup_name: str,
    threshold_pct: Decimal,
) -> Reconciled | None:
    """Elige el dato a guardar para una rueda.

    - Las dos fuentes: se guarda el principal y se marca si la divergencia supera el umbral.
    - Una sola: se guarda esa, sin validación cruzada (respaldo y divergencia nulos).
    - Ninguna: no hubo rueda (feriado o especie sin operaciones); no se guarda nada.
    """
    if primary is not None and backup is not None:
        divergence = divergence_pct(primary.cierre, backup.cierre)
        return Reconciled(
            valor=primary.cierre,
            volumen=primary.volumen,
            fuente=primary_name,
            valor_respaldo=backup.cierre,
            divergencia_pct=divergence,
            marcado=divergence > threshold_pct,
        )
    only = primary or backup
    if only is None:
        return None
    return Reconciled(
        valor=only.cierre,
        volumen=only.volumen,
        fuente=primary_name if only is primary else backup_name,
        valor_respaldo=None,
        divergencia_pct=None,
        marcado=False,
    )


def implied_rate(pesos: DailyBar, dollars: DailyBar) -> DailyBar:
    """Tipo de cambio implícito de un bono: precio en pesos / precio en dólares."""
    if pesos.fecha != dollars.fecha:
        raise ValueError("las dos patas del tipo de cambio deben ser de la misma rueda")
    rate = (pesos.cierre / dollars.cierre).quantize(PRICE_QUANTUM, ROUND_HALF_EVEN)
    return DailyBar(ticker="CCL", fecha=pesos.fecha, cierre=rate, volumen=None)
