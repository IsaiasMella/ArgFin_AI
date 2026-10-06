"""Cola de tareas: esquema de Procrastinate 3.10.0.

El SQL está congelado en `migrations/sql/` para que esta migración no cambie si se
actualiza la librería. Una versión nueva de Procrastinate con cambios de esquema se
incorpora con otra migración que aplique sus archivos de `procrastinate/sql/migrations`.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "sql" / "procrastinate-3.10.0.sql"


def upgrade() -> None:
    # Directo al driver y sin parámetros: el SQL tiene varias sentencias, bloques $$ y '%'.
    driver_connection = op.get_bind().connection.driver_connection
    if driver_connection is None:
        raise RuntimeError("La conexión de Alembic no tiene un driver psycopg activo.")
    driver_connection.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE obj record;
        BEGIN
            DROP TABLE IF EXISTS procrastinate_events, procrastinate_periodic_defers,
                procrastinate_jobs, procrastinate_workers CASCADE;
            FOR obj IN
                SELECT p.oid::regprocedure AS signature FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public' AND p.proname LIKE 'procrastinate\\_%'
            LOOP
                EXECUTE format('DROP FUNCTION %s', obj.signature);
            END LOOP;
            FOR obj IN
                SELECT t.typname FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = 'public' AND t.typname LIKE 'procrastinate\\_%'
                  AND t.typtype IN ('e', 'c')
            LOOP
                EXECUTE format('DROP TYPE %I', obj.typname);
            END LOOP;
        END $$
        """
    )
