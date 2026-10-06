"""Registro de llamadas a LLMs (costo, tokens, latencia).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tabla compartida: no guarda datos de usuario (a los LLMs solo van datos públicos).
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("proposito", sa.Text(), nullable=False),
        sa.Column("modelo", sa.Text(), nullable=False),
        sa.Column("version_prompt", sa.Text(), nullable=False),
        sa.Column("intento", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("costo_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("latencia_ms", sa.Integer(), nullable=False),
        sa.Column("exito", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
    )
    op.create_index("ix_llm_calls_creado_en", "llm_calls", ["creado_en"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_creado_en", table_name="llm_calls")
    op.drop_table("llm_calls")
