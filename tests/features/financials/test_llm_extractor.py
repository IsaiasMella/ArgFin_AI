"""Lo que el extractor le envía al LLM (T3.5)."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from brujula.core.llm.client import LLMPurpose
from brujula.core.llm.prompts import PromptRegistry
from brujula.features.financials.catalog import load_metric_catalog
from brujula.features.financials.llm_extractor import LLMStatementExtractor, PagePayload
from brujula.features.financials.verification import load_extraction_config
from tests.features.financials.test_verification import extraction

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parents[3]


@dataclass
class Result:
    output: Any
    model: str = "proveedor/modelo"
    cost_usd: Decimal = Decimal("0.03")


@dataclass
class FakeClient:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def complete(self, **kwargs: Any) -> Result:
        self.calls.append(kwargs)
        return Result(extraction())


async def test_envia_el_texto_y_asocia_cada_imagen_a_su_pagina() -> None:
    client = FakeClient()
    catalog = load_metric_catalog(ROOT / "config")
    prompt = PromptRegistry(ROOT / "prompts").get(load_extraction_config(ROOT / "config").prompt)
    extractor = LLMStatementExtractor(
        client,  # type: ignore[arg-type]  # mismo contrato que LLMClient.complete
        prompt,
        catalog,
        model="otro/modelo",
    )

    outcome = await extractor.extract(
        pages=[
            PagePayload(2, "Total del activo 1.500.000"),
            PagePayload(5, "", b"png-5"),
            PagePayload(7, "", b"png-7"),
        ],
        metrics=["activo_total", "ingresos"],
        fecha_cierre=date(2026, 6, 30),
        inicio_ejercicio=date(2026, 1, 1),
        tipo_balance="consolidado",
    )

    assert (outcome.model, outcome.cost_usd) == ("proveedor/modelo", Decimal("0.03"))
    [call] = client.calls
    assert call["purpose"] is LLMPurpose.EXTRACTION
    assert call["model"] == "otro/modelo"
    assert call["images"] == [b"png-5", b"png-7"]
    pages = call["variables"]["paginas"]
    assert "=== Página 2 ===\nTotal del activo 1.500.000" in pages
    assert "=== Página 5 ===\n(página escaneada: es la imagen adjunta 1 de 2)" in pages
    assert "=== Página 7 ===\n(página escaneada: es la imagen adjunta 2 de 2)" in pages
    assert call["variables"]["metricas"].startswith("- activo_total: ")
    # El LLM extrae a ciegas: no recibe ningún valor de la CNV.
    assert "1500000" not in str(call["variables"])
