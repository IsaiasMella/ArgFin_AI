"""Comandos de operación: `python -m brujula.cli <comando>`.

recifrar                Re-cifra con la clave actual los valores cifrados con una clave
                        anterior (paso 3 de la rotación, docs/adr/008).
sincronizar-universo    Carga config/universe.yaml en la base (docs/adr/010). Con --simular
                        muestra los cambios sin guardarlos.

Los comandos usan el rol de migraciones (dueño de las tablas).
"""

import argparse
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from brujula.core.config import Settings, get_settings
from brujula.core.security.crypto import FieldCipher
from brujula.core.security.encrypted_types import cipher_from_settings
from brujula.features.portfolios.maintenance import link_free_tickers
from brujula.features.portfolios.models import PRICE_CONTEXT, QUANTITY_CONTEXT
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="brujula.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("recifrar", help="re-cifra datos con la clave actual (rotación)")
    sync = commands.add_parser("sincronizar-universo", help="carga config/universe.yaml")
    sync.add_argument("--simular", action="store_true", help="muestra los cambios sin guardarlos")
    args = parser.parse_args(argv)

    settings = get_settings()
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
