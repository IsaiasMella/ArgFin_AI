"""Esquema de extracción de estados contables (T3.4, plan 7.2.4).

`FinancialStatementExtraction` es lo que un LLM devuelve al leer el PDF de un estado contable
(structured outputs, T3.5). Cada cifra trae su página y el texto tal cual figura en el
documento: el número se verifica contra el PDF con código, nunca se acepta solo porque el
modelo lo dijo (constitución, punto 2).
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MeasurementBase = Literal["nominal", "homogenea"]
DocumentUnit = Literal["unidades", "miles", "millones"]


class ExtractedMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrica: str = Field(description="Código del catálogo de métricas (config/metrics.yaml).")
    valor: Decimal = Field(
        description="Valor en la unidad del documento, con signo (negativo si figura entre "
        "paréntesis o con signo menos). Sin aplicar la unidad."
    )
    texto_original: str = Field(
        description="El número exactamente como aparece impreso, p. ej. '(1.234.567)'."
    )
    pagina: int = Field(ge=1, description="Página del PDF (empezando en 1) donde aparece.")
    periodo_inicio: date | None = Field(
        description="Inicio del período para flujos (resultados, flujo de efectivo); "
        "nulo para saldos del balance."
    )
    periodo_fin: date = Field(description="Fecha de cierre a la que corresponde la cifra.")
    es_comparativo: bool = Field(
        description="True si es la cifra del período anterior presentada como comparativa."
    )


class FinancialStatementExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    moneda: str = Field(description="Código ISO de la moneda de presentación, p. ej. ARS.")
    unidad: DocumentUnit = Field(description="Unidad en la que están expresadas las cifras.")
    base_medicion: MeasurementBase = Field(
        description="'homogenea' si está reexpresado en moneda de cierre (NIC 29); si no, "
        "'nominal'."
    )
    fecha_cierre: date
    tipo_balance: Literal["consolidado", "individual"]
    metricas: list[ExtractedMetric]
