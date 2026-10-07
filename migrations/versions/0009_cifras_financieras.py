"""Cifras financieras por métrica interna y período (T3.3).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tabla compartida (datos públicos de empresas, sin datos de usuario).
    op.create_table(
        "financial_facts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("metrica", sa.Text(), nullable=False),
        sa.Column("periodo_inicio", sa.Date(), nullable=True),
        sa.Column("periodo_fin", sa.Date(), nullable=False),
        sa.Column("valor", sa.Numeric(30, 6), nullable=False),
        sa.Column("moneda", sa.Text(), nullable=True),
        sa.Column("unidad", sa.Text(), nullable=False),
        sa.Column("base_medicion", sa.Text(), nullable=False),
        sa.Column("fecha_reexpresion", sa.Date(), nullable=True),
        sa.Column("es_comparativo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("fuente", sa.Text(), nullable=False),
        sa.Column("referencia", sa.Text(), nullable=False),
        sa.Column("pagina", sa.Integer(), nullable=True),
        sa.Column("formulario", sa.Text(), nullable=True),
        sa.Column("fecha_presentacion", sa.Date(), nullable=True),
        sa.Column("extractor_version", sa.Text(), nullable=False),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("fuente IN ('sec_xbrl', 'cnv')", name=op.f("ck_financial_facts_fuente")),
        sa.CheckConstraint(
            "base_medicion IN ('nominal', 'homogenea')",
            name=op.f("ck_financial_facts_base_medicion"),
        ),
        sa.CheckConstraint(
            "periodo_inicio IS NULL OR periodo_inicio <= periodo_fin",
            name=op.f("ck_financial_facts_periodo_ordenado"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_financial_facts_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_financial_facts_document_id_documents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_facts")),
        sa.UniqueConstraint(
            "company_id",
            "metrica",
            "periodo_inicio",
            "periodo_fin",
            "fuente",
            name=op.f("uq_financial_facts_metrica_periodo_fuente"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_financial_facts_company_id", "financial_facts", ["company_id"])


def downgrade() -> None:
    op.drop_table("financial_facts")
