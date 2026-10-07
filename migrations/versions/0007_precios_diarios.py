"""Cierres diarios y tipo de cambio CCL con validación cruzada (T2.3).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-07
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE_CHECK = "fuente IN ('byma', 'data912')"


def _audit_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("divergencia_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("marcado", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    # Tablas compartidas (sin datos de usuario): las escribe la tarea diaria del worker.
    op.create_table(
        "prices_daily",
        sa.Column("instrument_id", sa.Uuid(), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("cierre", sa.Numeric(20, 6), nullable=False),
        sa.Column("volumen", sa.Numeric(24, 4), nullable=True),
        sa.Column("fuente", sa.Text(), nullable=False),
        sa.Column("cierre_respaldo", sa.Numeric(20, 6), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint("cierre > 0", name=op.f("ck_prices_daily_cierre_positivo")),
        sa.CheckConstraint(SOURCE_CHECK, name=op.f("ck_prices_daily_fuente")),
        sa.CheckConstraint(
            "divergencia_pct >= 0", name=op.f("ck_prices_daily_divergencia_no_negativa")
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_prices_daily_instrument_id_instruments"),
        ),
        sa.PrimaryKeyConstraint("instrument_id", "fecha", name=op.f("pk_prices_daily")),
    )
    op.create_table(
        "fx_daily",
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("ccl", sa.Numeric(20, 6), nullable=False),
        sa.Column("fuente", sa.Text(), nullable=False),
        sa.Column("ccl_respaldo", sa.Numeric(20, 6), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint("ccl > 0", name=op.f("ck_fx_daily_ccl_positivo")),
        sa.CheckConstraint(SOURCE_CHECK, name=op.f("ck_fx_daily_fuente")),
        sa.CheckConstraint(
            "divergencia_pct >= 0", name=op.f("ck_fx_daily_divergencia_no_negativa")
        ),
        sa.PrimaryKeyConstraint("fecha", name=op.f("pk_fx_daily")),
    )


def downgrade() -> None:
    op.drop_table("fx_daily")
    op.drop_table("prices_daily")
