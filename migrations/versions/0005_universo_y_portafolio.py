"""Universo (empresas e instrumentos) y posiciones del portafolio con cifrado y RLS.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Tablas compartidas (sin datos de usuario). Se cargan desde config/universe.yaml (T2.1).
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("nombre", sa.Text(), nullable=False),
        sa.Column("sector", sa.Text(), nullable=False),
        sa.Column("pais", sa.Text(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("cik_sec", sa.Text(), nullable=True),
        sa.Column("url_relacion_inversores", sa.Text(), nullable=True),
        sa.Column("activa", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.CheckConstraint("tipo IN ('ar_equity', 'cedear')", name=op.f("ck_companies_tipo")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
    )
    op.create_table(
        "instruments",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("ticker_byma", sa.Text(), nullable=False),
        sa.Column("ticker_origen", sa.Text(), nullable=True),
        sa.Column("ratio_cedear", sa.Numeric(12, 4), nullable=True),
        sa.Column("moneda", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_instruments_company_id_companies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
        sa.UniqueConstraint("ticker_byma", name=op.f("uq_instruments_ticker_byma")),
    )

    op.create_table(
        "holdings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_id", sa.Uuid(), nullable=True),
        sa.Column("ticker_libre", sa.Text(), nullable=True),
        sa.Column("cantidad_cifrada", sa.LargeBinary(), nullable=False),
        sa.Column("precio_promedio_cifrado", sa.LargeBinary(), nullable=True),
        sa.Column("moneda_precio", sa.Text(), nullable=True),
        sa.Column("broker", sa.Text(), nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Exactamente uno: instrumento del universo o ticker libre (solo precio).
        sa.CheckConstraint(
            "(instrument_id IS NULL) <> (ticker_libre IS NULL)",
            name=op.f("ck_holdings_instrumento_o_ticker"),
        ),
        sa.CheckConstraint(
            "(precio_promedio_cifrado IS NULL) = (moneda_precio IS NULL)",
            name=op.f("ck_holdings_precio_con_moneda"),
        ),
        sa.CheckConstraint(
            "moneda_precio IN ('ARS', 'USD')", name=op.f("ck_holdings_moneda_precio")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_holdings_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_holdings_instrument_id_instruments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holdings")),
    )
    op.create_index("ix_holdings_user_id", "holdings", ["user_id"])
    op.execute("ALTER TABLE holdings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE holdings FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY holdings_propio ON holdings
            USING (user_id = app_current_user_id())
            WITH CHECK (user_id = app_current_user_id())
        """
    )
    op.execute(
        "CREATE POLICY holdings_duenio ON holdings TO CURRENT_USER USING (true) WITH CHECK (true)"
    )


def downgrade() -> None:
    op.drop_index("ix_holdings_user_id", table_name="holdings")
    op.drop_table("holdings")
    op.drop_table("instruments")
    op.drop_table("companies")
