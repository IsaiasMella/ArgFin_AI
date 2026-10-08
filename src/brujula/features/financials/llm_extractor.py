"""Extracción de un estado contable con LLM (tercera fuente de la verificación, T3.5).

El LLM no ve los datos de la CNV: extrae de forma independiente, para que una coincidencia
signifique algo. Las páginas escaneadas (sin texto) van como imagen al modelo multimodal.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from brujula.core.llm.client import LLMClient, LLMPurpose
from brujula.core.llm.prompts import Prompt
from brujula.features.financials.catalog import MetricCatalog
from brujula.features.financials.schemas import FinancialStatementExtraction


@dataclass(frozen=True)
class PagePayload:
    numero: int  # 1-based, como en el PDF
    texto: str
    imagen: bytes | None = None  # PNG si la página es escaneada


@dataclass(frozen=True)
class ExtractionOutcome:
    extraction: FinancialStatementExtraction
    model: str
    cost_usd: Decimal


class StatementExtractor(Protocol):
    async def extract(
        self,
        *,
        pages: Sequence[PagePayload],
        metrics: Sequence[str],
        fecha_cierre: date,
        inicio_ejercicio: date,
        tipo_balance: str,
    ) -> ExtractionOutcome: ...


class LLMStatementExtractor:
    def __init__(
        self,
        client: LLMClient,
        prompt: Prompt,
        catalog: MetricCatalog,
        *,
        model: str | None = None,
    ) -> None:
        self._client = client
        self._prompt = prompt
        self._catalog = catalog
        self._model = model

    async def extract(
        self,
        *,
        pages: Sequence[PagePayload],
        metrics: Sequence[str],
        fecha_cierre: date,
        inicio_ejercicio: date,
        tipo_balance: str,
    ) -> ExtractionOutcome:
        listing = "\n".join(f"- {code}: {self._catalog.metricas[code].nombre}" for code in metrics)
        scanned = [page for page in pages if page.imagen is not None]
        image_of = {page.numero: index for index, page in enumerate(scanned, start=1)}
        text = "\n\n".join(
            f"=== Página {page.numero} ===\n"
            + (
                page.texto
                if page.imagen is None
                else f"(página escaneada: es la imagen adjunta {image_of[page.numero]}"
                f" de {len(scanned)})"
            )
            for page in pages
        )
        result = await self._client.complete(
            purpose=LLMPurpose.EXTRACTION,
            prompt=self._prompt,
            variables={
                "tipo_balance": tipo_balance,
                "fecha_cierre": fecha_cierre.isoformat(),
                "inicio_ejercicio": inicio_ejercicio.isoformat(),
                "metricas": listing,
                "paginas": text,
            },
            output_type=FinancialStatementExtraction,
            model=self._model,
            images=[page.imagen for page in scanned if page.imagen is not None],
        )
        return ExtractionOutcome(
            extraction=result.output, model=result.model, cost_usd=result.cost_usd
        )
