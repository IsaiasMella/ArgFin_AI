"""Claves estables para sincronizar el universo desde config/universe.yaml (T2.1).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `clave` identifica a la empresa en el YAML aunque cambie su nombre. Las filas previas (si
    # las hubiera) reciben su id como clave y quedan inactivas hasta la próxima sincronización.
    op.add_column("companies", sa.Column("clave", sa.Text(), nullable=True))
    op.execute("UPDATE companies SET clave = id::text, activa = false WHERE clave IS NULL")
    op.alter_column("companies", "clave", nullable=False)
    op.create_unique_constraint(op.f("uq_companies_clave"), "companies", ["clave"])

    # Un instrumento que sale del YAML no se borra (puede haber posiciones que lo usan).
    op.add_column(
        "instruments",
        sa.Column("activo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("instruments", "activo")
    op.drop_constraint(op.f("uq_companies_clave"), "companies", type_="unique")
    op.drop_column("companies", "clave")
