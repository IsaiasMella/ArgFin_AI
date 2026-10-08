"""Diferencias de la verificación triple de estados contables (T3.5).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Lo que la sección de admin "Revisiones manuales" (T6.4) le muestra a una persona.
    op.create_table(
        "verification_issues",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("metrica", sa.Text(), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("detalle", sa.Text(), nullable=False),
        sa.Column("valor_cnv", sa.Numeric(30, 6), nullable=True),
        sa.Column("valor_llm", sa.Numeric(30, 6), nullable=True),
        sa.Column("pagina", sa.Integer(), nullable=True),
        sa.Column("bloqueante", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("modelo", sa.Text(), nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_verification_issues_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_issues")),
    )
    op.create_index("ix_verification_issues_document_id", "verification_issues", ["document_id"])
    op.add_column(
        "documents", sa.Column("verificado_en", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("costo_verificacion_usd", sa.Numeric(12, 6), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("documents", "costo_verificacion_usd")
    op.drop_column("documents", "verificado_en")
    op.drop_table("verification_issues")
