"""Esquema base: roles, usuarios y Row Level Security.

Revision ID: 0001
Revises:
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Usuario de la transacción actual, o NULL si no hay contexto. NULLIF cubre el caso en
    # que la variable ya se usó en la sesión: fuera de la transacción vale '' y no NULL.
    op.execute(
        """
        CREATE FUNCTION app_current_user_id() RETURNS uuid
            LANGUAGE sql STABLE PARALLEL SAFE
            AS $$ SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid $$
        """
    )

    roles = op.create_table(
        "roles",
        sa.Column("codigo", sa.Text(), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("codigo", name=op.f("pk_roles")),
    )
    op.bulk_insert(
        roles,
        [
            {"codigo": "usuario", "descripcion": "Usuario registrado"},
            {"codigo": "admin", "descripcion": "Administración (p. ej. mapa de exposición)"},
        ],
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("nombre", sa.Text(), nullable=True),
        sa.Column("proveedor_oauth", sa.Text(), nullable=False),
        sa.Column("sub_oauth", sa.Text(), nullable=False),
        sa.Column("rol", sa.Text(), server_default=sa.text("'usuario'"), nullable=False),
        sa.Column("plan", sa.Text(), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("borrado_en", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "plan IN ('gratis', 'pro', 'fundador')", name=op.f("ck_users_plan_valido")
        ),
        sa.ForeignKeyConstraint(["rol"], ["roles.codigo"], name=op.f("fk_users_rol_roles")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("proveedor_oauth", "sub_oauth", name="uq_users_proveedor_sub"),
    )
    op.execute("CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))")

    # RLS: FORCE la aplica también al dueño de la tabla. Ver docs/adr/004.
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY users_propio ON users
            USING (id = app_current_user_id())
            WITH CHECK (id = app_current_user_id())
        """
    )


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("roles")
    op.execute("DROP FUNCTION app_current_user_id()")
