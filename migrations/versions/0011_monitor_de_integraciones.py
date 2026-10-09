"""Corridas e incidentes del monitor de integraciones (T3.7).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("fuente", sa.Text(), nullable=False),
        sa.Column("objeto", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("formato", sa.Boolean(), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_runs")),
    )
    op.create_index(
        "ix_integration_runs_fuente_objeto",
        "integration_runs",
        ["fuente", "objeto", "creado_en"],
    )
    op.create_table(
        "integration_incidents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("fuente", sa.Text(), nullable=False),
        sa.Column("objeto", sa.Text(), nullable=False),
        sa.Column("detalle", sa.Text(), nullable=False),
        sa.Column("abierto_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("avisado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cerrado_en", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "tipo IN ('falla_repetida', 'formato', 'documento_faltante')",
            name=op.f("ck_integration_incidents_tipo"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_incidents")),
    )
    # Un solo incidente abierto por problema: es lo que garantiza un único aviso.
    op.create_index(
        "uq_integration_incidents_abierto",
        "integration_incidents",
        ["tipo", "fuente", "objeto"],
        unique=True,
        postgresql_where=sa.text("cerrado_en IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("integration_incidents")
    op.drop_table("integration_runs")
