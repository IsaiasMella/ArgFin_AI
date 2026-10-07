"""Documentos de empresas y datos estructurados de la CNV (T3.2).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tablas compartidas (documentos públicos, sin datos de usuario).
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("fuente", sa.Text(), nullable=False),
        sa.Column("clave_externa", sa.Text(), nullable=False),
        sa.Column("periodo", sa.Date(), nullable=True),
        sa.Column("url_origen", sa.Text(), nullable=False),
        sa.Column("nombre_archivo", sa.Text(), nullable=False),
        sa.Column("tipo_contenido", sa.Text(), nullable=False),
        sa.Column("hash_sha256", sa.Text(), nullable=False),
        sa.Column("tamano_bytes", sa.Integer(), nullable=False),
        sa.Column("ruta_almacenada", sa.Text(), nullable=False),
        sa.Column("fecha_publicacion", sa.Date(), nullable=True),
        sa.Column("estado", sa.Text(), server_default=sa.text("'descargado'"), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "tipo IN ('estado_contable', 'resena_informativa', 'memoria_anual',"
            " 'comunicado_resultados')",
            name=op.f("ck_documents_tipo"),
        ),
        sa.CheckConstraint(
            "fuente IN ('cnv', 'sec', 'sitio_inversores')", name=op.f("ck_documents_fuente")
        ),
        sa.CheckConstraint(
            "estado IN ('descargado', 'validado', 'revision_manual', 'error')",
            name=op.f("ck_documents_estado"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_documents_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint(
            "fuente", "clave_externa", name=op.f("uq_documents_fuente_clave_externa")
        ),
    )
    op.create_index("ix_documents_company_id", "documents", ["company_id"])
    op.create_index("ix_documents_hash_sha256", "documents", ["hash_sha256"])

    op.create_table(
        "cnv_statements",
        sa.Column("presentacion_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("fecha_cierre", sa.Date(), nullable=False),
        sa.Column("periodicidad", sa.Text(), nullable=False),
        sa.Column("tipo_balance", sa.Text(), nullable=False),
        sa.Column("moneda", sa.Text(), nullable=True),
        sa.Column("unidad", sa.Text(), nullable=True),
        sa.Column("norma_contable", sa.Text(), nullable=True),
        sa.Column("cuentas", JSONB(), nullable=False),
        sa.Column("url_origen", sa.Text(), nullable=False),
        sa.Column("fecha_publicacion", sa.Date(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "tipo_balance IN ('consolidado', 'individual')",
            name=op.f("ck_cnv_statements_tipo_balance"),
        ),
        sa.CheckConstraint(
            "periodicidad IN ('trimestral', 'anual')",
            name=op.f("ck_cnv_statements_periodicidad"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_cnv_statements_company_id_companies")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_cnv_statements_document_id_documents"),
        ),
        sa.PrimaryKeyConstraint("presentacion_id", name=op.f("pk_cnv_statements")),
    )
    op.create_index("ix_cnv_statements_company_id", "cnv_statements", ["company_id"])

    op.create_table(
        "document_checks",
        sa.Column("fuente", sa.Text(), nullable=False),
        sa.Column("clave_externa", sa.Text(), nullable=False),
        sa.Column("resultado", sa.Text(), nullable=False),
        sa.Column(
            "revisado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("fuente", "clave_externa", name=op.f("pk_document_checks")),
    )


def downgrade() -> None:
    op.drop_table("document_checks")
    op.drop_table("cnv_statements")
    op.drop_table("documents")
