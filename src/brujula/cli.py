"""Comandos de operación: `python -m brujula.cli <comando>`.

recifrar                Re-cifra con la clave actual los valores cifrados con una clave
                        anterior (paso 3 de la rotación, docs/adr/008).
sincronizar-universo    Carga config/universe.yaml en la base (docs/adr/010). Con --simular
                        muestra los cambios sin guardarlos.
actualizar-precios      Corre la tarea de precios y CCL para un rango de fechas (docs/adr/012),
                        p. ej. para cargar historia. Por defecto, la última semana.
ingestar-documentos     Busca y descarga documentos nuevos de las empresas argentinas
                        (docs/adr/014). Opcional: --empresa CLAVE y --desde AAAA-MM-DD.
actualizar-sec          Actualiza las cifras XBRL de la SEC de los subyacentes de CEDEARs
                        (docs/adr/015). Opcional: --empresa CLAVE.
verificar-estados       Verificación triple de los estados contables descargados
                        (docs/adr/017). Opcional: --empresa CLAVE, --limite N y --modelo.

`recifrar` y `sincronizar-universo` usan el rol de migraciones (dueño de las tablas);
`actualizar-precios`, `ingestar-documentos`, `actualizar-sec` y `verificar-estados`, el de
la app, igual que el worker.
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from brujula.core.config import Settings, get_settings
from brujula.core.security.crypto import FieldCipher
from brujula.core.security.encrypted_types import cipher_from_settings
from brujula.features.documents.ingest import CompanyReport
from brujula.features.documents.settings import DocumentsConfigError
from brujula.features.documents.tasks import ingest_documents
from brujula.features.financials.catalog import FinancialsConfigError
from brujula.features.financials.tasks import refresh_sec_facts, verify_statements
from brujula.features.portfolios.maintenance import link_free_tickers
from brujula.features.portfolios.models import PRICE_CONTEXT, QUANTITY_CONTEXT
from brujula.features.prices.daily import (
    DailyRunReport,
    PricesConfigError,
    PricesUnavailableError,
)
from brujula.features.prices.tasks import LOOKBACK_DAYS, update_prices
from brujula.features.universe.catalog import UNIVERSE_FILE, UniverseError, load_universe
from brujula.features.universe.sync import SyncResult, sync_universe

BATCH_SIZE = 500


@dataclass(frozen=True)
class ReencryptResult:
    revisadas: int
    recifradas: int


def reencrypt_holdings(engine: Engine, cipher: FieldCipher) -> ReencryptResult:
    """Recorre `holdings` en lotes y re-cifra los valores con clave vieja. Idempotente."""
    reviewed = rewritten = 0
    last_id = None
    while True:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, cantidad_cifrada, precio_promedio_cifrado FROM holdings"
                    " WHERE (CAST(:last AS uuid) IS NULL OR id > CAST(:last AS uuid))"
                    " ORDER BY id LIMIT :limit"
                ),
                {"last": last_id, "limit": BATCH_SIZE},
            ).all()
            if not rows:
                return ReencryptResult(revisadas=reviewed, recifradas=rewritten)
            for row_id, quantity, price in rows:
                reviewed += 1
                stale_quantity = cipher.needs_reencryption(quantity)
                stale_price = price is not None and cipher.needs_reencryption(price)
                if not (stale_quantity or stale_price):
                    continue
                conn.execute(
                    text(
                        "UPDATE holdings SET cantidad_cifrada = :quantity,"
                        " precio_promedio_cifrado = :price WHERE id = :id"
                    ),
                    {
                        "id": row_id,
                        "quantity": cipher.reencrypt(quantity, QUANTITY_CONTEXT)
                        if stale_quantity
                        else quantity,
                        "price": cipher.reencrypt(price, PRICE_CONTEXT) if stale_price else price,
                    },
                )
                rewritten += 1
            last_id = str(rows[-1][0])


@dataclass(frozen=True)
class UniverseSyncReport:
    cambios: SyncResult
    posiciones_vinculadas: int
    simulado: bool


def sync_universe_file(settings: Settings, engine: Engine, *, dry_run: bool) -> UniverseSyncReport:
    """Valida el YAML y lo aplica en una sola transacción; en simulación, la deshace."""
    universe = load_universe(settings.config_dir / UNIVERSE_FILE)
    with Session(engine) as session:
        changes = sync_universe(session, universe)
        linked = link_free_tickers(session)
        if dry_run:
            session.rollback()
        else:
            session.commit()
    return UniverseSyncReport(cambios=changes, posiciones_vinculadas=linked, simulado=dry_run)


def _print_sync_report(report: UniverseSyncReport) -> None:
    title = "Simulación (no se guardó nada)" if report.simulado else "Universo sincronizado"
    print(f"{title}:")
    for name, value in vars(report.cambios).items():
        print(f"  {name.replace('_', ' ')}: {value}")
    print(f"  posiciones vinculadas al universo: {report.posiciones_vinculadas}")


def _print_prices_report(report: DailyRunReport) -> None:
    print(f"Precios del {report.desde} al {report.hasta}:")
    for name, value in vars(report).items():
        if name not in {"desde", "hasta", "errores"}:
            print(f"  {name.replace('_', ' ')}: {value}")
    for provider, errors in report.errores.items():
        print(f"  sin datos de {provider}: {', '.join(f'{t} ({m})' for t, m in errors.items())}")


def _run_prices(settings: Settings, start: date | None, end: date | None) -> None:
    end = end or datetime.now(ZoneInfo(settings.app_timezone)).date()
    start = start or end - timedelta(days=LOOKBACK_DAYS - 1)
    if start > end:
        raise SystemExit("error: --desde es posterior a --hasta")
    # psycopg asíncrono no funciona con el event loop por defecto de Windows (Proactor).
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        report = asyncio.run(
            update_prices(settings, start=start, end=end), loop_factory=loop_factory
        )
    except (PricesConfigError, PricesUnavailableError) as exc:
        raise SystemExit(f"error: {exc}") from None
    _print_prices_report(report)


def _run_ingestion(settings: Settings, only: str | None, since: date | None) -> None:
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        reports: list[CompanyReport] = asyncio.run(
            ingest_documents(settings, since=since, only=only), loop_factory=loop_factory
        )
    except (DocumentsConfigError, UniverseError) as exc:
        raise SystemExit(f"error: {exc}") from None
    if not reports:
        raise SystemExit("error: ninguna empresa argentina coincide con --empresa")
    for report in reports:
        nuevos = ", ".join(f"{tipo}: {n}" for tipo, n in sorted(report.nuevos.items())) or "nada"
        print(f"{report.clave}: {nuevos}; estados estructurados: {report.estados_estructurados}")
        for fuente, motivo in report.errores:
            print(f"  error en {fuente}: {motivo}")


def _run_sec(settings: Settings, only: str | None) -> None:
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        reports = asyncio.run(refresh_sec_facts(settings, only=only), loop_factory=loop_factory)
    except (FinancialsConfigError, UniverseError) as exc:
        raise SystemExit(f"error: {exc}") from None
    if not reports:
        raise SystemExit("error: ningún CEDEAR con CIK coincide con --empresa")
    for report in reports:
        print(f"{report.clave}: cifras {report.cifras}, escritas {report.escritas}")
        for fuente, motivo in report.errores:
            print(f"  error en {fuente}: {motivo}")


def _run_verification(
    settings: Settings, only: str | None, limit: int | None, model: str | None
) -> None:
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        reports = asyncio.run(
            verify_statements(settings, only=only, limit=limit, model=model),
            loop_factory=loop_factory,
        )
    except (FinancialsConfigError, UniverseError) as exc:
        raise SystemExit(f"error: {exc}") from None
    if not reports:
        print("No hay estados contables pendientes de verificar.")
    for report in reports:
        status = report.error or report.estado
        print(f"{report.clave} al {report.fecha_cierre}: {status} (USD {report.costo_usd})")
        for issue in report.diferencias:
            values = f" CNV={issue.valor_cnv} LLM={issue.valor_llm}" if issue.valor_cnv else ""
            print(f"  {issue.motivo} {issue.metrica or ''} {issue.detalle}{values}".rstrip())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="brujula.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("recifrar", help="re-cifra datos con la clave actual (rotación)")
    sync = commands.add_parser("sincronizar-universo", help="carga config/universe.yaml")
    sync.add_argument("--simular", action="store_true", help="muestra los cambios sin guardarlos")
    prices = commands.add_parser("actualizar-precios", help="precios y CCL para un rango")
    prices.add_argument("--desde", type=date.fromisoformat, help="AAAA-MM-DD")
    prices.add_argument("--hasta", type=date.fromisoformat, help="AAAA-MM-DD (por defecto, hoy)")
    documents = commands.add_parser("ingestar-documentos", help="documentos nuevos de empresas")
    documents.add_argument("--empresa", help="clave de la empresa en universe.yaml")
    documents.add_argument("--desde", type=date.fromisoformat, help="AAAA-MM-DD")
    sec = commands.add_parser("actualizar-sec", help="cifras XBRL de los CEDEARs")
    sec.add_argument("--empresa", help="clave de la empresa en universe.yaml")
    check = commands.add_parser("verificar-estados", help="verificación triple de estados")
    check.add_argument("--empresa", help="clave de la empresa en universe.yaml")
    check.add_argument("--limite", type=int, help="máximo de estados a verificar")
    check.add_argument("--modelo", help="modelo LLM a usar en lugar de LLM_EXTRACTION_MODEL")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.command == "verificar-estados":
        _run_verification(settings, args.empresa, args.limite, args.modelo)
        return
    if args.command == "actualizar-sec":
        _run_sec(settings, args.empresa)
        return
    if args.command == "actualizar-precios":
        _run_prices(settings, args.desde, args.hasta)
        return
    if args.command == "ingestar-documentos":
        _run_ingestion(settings, args.empresa, args.desde)
        return
    engine = create_engine(settings.database_url_migrations.get_secret_value())
    try:
        if args.command == "recifrar":
            result = reencrypt_holdings(engine, cipher_from_settings(settings))
            print(f"posiciones revisadas: {result.revisadas}, re-cifradas: {result.recifradas}")
        else:
            try:
                report = sync_universe_file(settings, engine, dry_run=args.simular)
            except UniverseError as exc:
                raise SystemExit(f"error: {exc}") from None
            _print_sync_report(report)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
