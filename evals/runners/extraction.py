"""Eval de extracción de estados contables (T3.6, ADR 018).

    python -m evals.runners.extraction preparar --empresa GGAL --empresa YPF ...
    python -m evals.runners.extraction correr [--modelo proveedor/modelo ...]

`preparar` precarga el dataset de referencia con las cifras de la CNV que están impresas en
el PDF; cada caso queda pendiente hasta que una persona lo verifique contra el PDF y complete
`verificado_por`. `correr` extrae cada caso con cada modelo (por defecto
`LLM_EXTRACTION_MODEL` y, si está, `LLM_EXTRACTION_FALLBACK_MODEL`), igual que en la
verificación de T3.5, y guarda el resultado por fecha, modelo y versión de prompt.
"""

import argparse
import asyncio
import hashlib
import json
import re
import time
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from sqlalchemy.ext.asyncio import AsyncEngine

from brujula.core.config import Settings, get_settings
from brujula.core.db import create_engine
from brujula.core.llm.client import LLMError
from brujula.features.financials.catalog import MetricCatalog, load_metric_catalog
from brujula.features.financials.llm_extractor import ExtractionOutcome, PagePayload
from brujula.features.financials.pipeline import (
    PendingStatement,
    PreparedStatement,
    StatementVerifier,
)
from brujula.features.financials.tasks import build_statement_verifier
from brujula.features.financials.verification import load_extraction_config
from evals.runners.scoring import (
    CaseResult,
    ExtractionCase,
    ExtractionDataset,
    ExtractionEvalConfig,
    ModelSummary,
    cheapest_passing,
    comparison_table,
    expected_figures,
    failed_case,
    load_dataset,
    load_evals_config,
    merge_cases,
    score_case,
    summarize,
)

BalanceKind = Literal["consolidado", "individual"]

DATASET_HEADER = """\
# Dataset de referencia de la extracción de estados contables (T3.6, docs/adr/018).
# Precargado con `python -m evals.runners.extraction preparar`: cifras de la CNV que también
# están impresas en el PDF. PENDIENTE DE VERIFICACIÓN HUMANA: para cada caso, abrir el PDF
# (documents.hash_sha256 = documento_sha256), comprobar cada cifra en su página, corregir lo
# que haga falta y completar `verificado_por: "Nombre, AAAA-MM-DD"`. El GATE de T3.6 solo se
# evalúa cuando todos los casos están verificados. `preparar` nunca pisa un caso verificado.
"""


class _NoLLM:
    """`preparar` no llama al LLM (no hace falta tener las claves configuradas)."""

    async def extract(self, *, pages: Sequence[PagePayload], **_: Any) -> ExtractionOutcome:
        raise RuntimeError("preparar no usa el LLM")


async def _statement(verifier: StatementVerifier, case: ExtractionCase) -> PendingStatement | None:
    for item in await verifier.latest(only=case.empresa):
        if item.document.hash_sha256 == case.documento_sha256:
            return item
    return None


# --- preparar -----------------------------------------------------------------------------


async def prepare_dataset(
    settings: Settings, engine: AsyncEngine, companies: Sequence[str], quarters: int
) -> tuple[list[ExtractionCase], list[str]]:
    catalog = load_metric_catalog(settings.config_dir)
    currencies = load_extraction_config(settings.config_dir).monedas_cnv
    verifier = build_statement_verifier(settings, engine, extractor=_NoLLM())
    cases, notes = [], []
    for company in companies:
        items = await verifier.latest(only=company)
        # Consolidado si lo hay (es el que se publica); los últimos `quarters` cierres.
        kinds = {item.statement.tipo_balance for item in items}
        kind = "consolidado" if "consolidado" in kinds else "individual"
        chosen = [item for item in items if item.statement.tipo_balance == kind][-quarters:]
        if len(chosen) < quarters:
            notes.append(f"{company}: {len(chosen)} de {quarters} trimestres descargados")
        for item in chosen:
            prepared = await verifier.prepare(item)
            unit, figures = expected_figures(
                prepared.mapped.facts, prepared.pages, prepared.context.unidad_cnv, catalog
            )
            currency = currencies.get(item.statement.moneda or "")
            if unit is None or currency is None:
                notes.append(f"{company} {item.statement.fecha_cierre}: ninguna cifra en el PDF")
                continue
            cases.append(
                ExtractionCase(
                    empresa=company,
                    fecha_cierre=item.statement.fecha_cierre,
                    tipo_balance=cast(BalanceKind, kind),
                    documento_sha256=item.document.hash_sha256,
                    moneda=currency,
                    unidad=unit,
                    base_medicion="homogenea",
                    cifras=figures,
                )
            )
    return cases, notes


def write_dataset(path: Path, cases: Sequence[ExtractionCase]) -> None:
    data = ExtractionDataset(version=1, casos=list(cases)).model_dump(mode="json")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
    path.write_text(DATASET_HEADER + "\n" + body, encoding="utf-8")


# --- correr -------------------------------------------------------------------------------


async def run_eval(
    cases: Sequence[ExtractionCase],
    models: Sequence[str],
    verifiers: dict[str, StatementVerifier],
    preparer: StatementVerifier,
    *,
    catalog: MetricCatalog,
    tolerance: Decimal,
) -> dict[str, list[CaseResult]]:
    """Cada caso con cada modelo. El documento se prepara (OCR incluido) una sola vez."""
    results: dict[str, list[CaseResult]] = {model: [] for model in models}
    for case in cases:
        item = await _statement(preparer, case)
        prepared: PreparedStatement | None = None
        if item is not None:
            prepared = await preparer.prepare(item)
        for model in models:
            if item is None or prepared is None:
                results[model].append(failed_case(case, "documento no encontrado"))
                continue
            started = time.perf_counter()
            try:
                outcome = await verifiers[model].extract(item, prepared)
            except LLMError as exc:
                results[model].append(failed_case(case, f"{type(exc).__name__}: {exc}"))
                continue
            result = score_case(case, outcome.extraction, catalog, tolerance)
            result.costo_usd = outcome.cost_usd
            result.latencia_s = time.perf_counter() - started
            results[model].append(result)
    return results


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._@-]+", "-", text).strip("-")


def save_results(
    config: ExtractionEvalConfig,
    dataset_path: Path,
    prompt: str,
    summaries: Sequence[ModelSummary],
    results: dict[str, list[CaseResult]],
    now: datetime,
) -> list[Path]:
    config.resultados.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%dT%H%M")
    digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    paths = []
    for summary in summaries:
        path = config.resultados / f"{stamp}_{_slug(summary.modelo)}_{_slug(prompt)}.json"
        payload = {
            "fecha": now.isoformat(),
            "modelo": summary.modelo,
            "prompt": prompt,
            "dataset_sha256": digest,
            "umbrales": config.model_dump(mode="json"),
            "resumen": {
                **asdict(summary),
                "exactitud_pct": summary.exactitud_pct,
                "cabecera_pct": summary.cabecera_pct,
                "gate": summary.gate(config),
            },
            "casos": [asdict(result) for result in results[summary.modelo]],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        paths.append(path)
    table = config.resultados / f"{stamp}_comparacion.md"
    table.write_text(comparison_table(summaries, config) + "\n", encoding="utf-8")
    return [*paths, table]


# --- CLI ----------------------------------------------------------------------------------


async def _prepare(args: argparse.Namespace) -> None:
    settings = get_settings()
    config = load_evals_config(settings.config_dir).extraccion
    engine = create_engine(settings)
    try:
        cases, notes = await prepare_dataset(settings, engine, args.empresa, args.trimestres)
    finally:
        await engine.dispose()
    existing = load_dataset(config.dataset).casos if config.dataset.exists() else []
    merged = merge_cases(existing, cases)
    write_dataset(config.dataset, merged)
    print(f"{config.dataset}: {len(merged)} casos ({len(cases)} preparados ahora).")
    for note in notes:
        print(f"  aviso: {note}")


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    config = load_evals_config(settings.config_dir).extraccion
    cases = load_dataset(config.dataset).casos
    if args.empresa:
        cases = [case for case in cases if case.empresa in args.empresa]
    cases = cases[: args.limite]
    models = args.modelo or [
        m
        for m in (settings.llm_extraction_model, settings.llm_extraction_fallback_model)
        if m is not None
    ]
    catalog = load_metric_catalog(settings.config_dir)
    prompt = load_extraction_config(settings.config_dir).prompt
    engine = create_engine(settings)
    try:
        verifiers = {m: build_statement_verifier(settings, engine, model=m) for m in models}
        results = await run_eval(
            cases,
            models,
            verifiers,
            verifiers[models[0]],
            catalog=catalog,
            tolerance=config.tolerancia_pct,
        )
    finally:
        await engine.dispose()
    summaries = [summarize(model, results[model]) for model in models]
    for path in save_results(config, config.dataset, prompt, summaries, results, datetime.now(UTC)):
        print(f"guardado: {path}")
    print(comparison_table(summaries, config))
    best = cheapest_passing(summaries, config)
    if best is None:
        print("Ningún modelo cumple los umbrales.")
    else:
        print(f"Más barato que cumple los umbrales: {best.modelo}")
    if any(s.gate(config) == "no_evaluable" for s in summaries):
        print("GATE no evaluable: el dataset no está completo o tiene casos sin verificar a mano.")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.runners.extraction")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("preparar", help="precargar el dataset de referencia")
    prepare.add_argument("--empresa", action="append", required=True, help="clave del universo")
    prepare.add_argument("--trimestres", type=int, default=4, help="últimos N cierres")
    run = commands.add_parser("correr", help="correr la eval con uno o más modelos")
    run.add_argument("--modelo", action="append", help="proveedor/modelo (repetible)")
    run.add_argument("--empresa", action="append", help="solo estas empresas")
    run.add_argument("--limite", type=int, help="máximo de casos")
    args = parser.parse_args(argv)
    asyncio.run(_prepare(args) if args.command == "preparar" else _run(args))


if __name__ == "__main__":
    main()
