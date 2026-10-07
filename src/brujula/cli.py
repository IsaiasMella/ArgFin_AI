"""Comandos de operación: `python -m brujula.cli <comando>`.

recifrar   Re-cifra con la clave actual los valores cifrados con una clave anterior
           (paso 3 de la rotación, docs/adr/008). Usa el rol de migraciones.
"""

import argparse
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, text

from brujula.core.config import get_settings
from brujula.core.security.crypto import FieldCipher
from brujula.core.security.encrypted_types import cipher_from_settings
from brujula.features.portfolios.models import PRICE_CONTEXT, QUANTITY_CONTEXT

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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="brujula.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("recifrar", help="re-cifra datos con la clave actual (rotación)")
    parser.parse_args(argv)

    settings = get_settings()
    engine = create_engine(settings.database_url_migrations.get_secret_value())
    try:
        result = reencrypt_holdings(engine, cipher_from_settings(settings))
    finally:
        engine.dispose()
    print(f"posiciones revisadas: {result.revisadas}, re-cifradas: {result.recifradas}")


if __name__ == "__main__":
    main()
