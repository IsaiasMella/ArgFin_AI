"""Login con OAuth: sesiones, transacciones OAuth y funciones SECURITY DEFINER.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Con FORCE RLS el dueño también está sujeto a las políticas. Esta política habilita
    # todo SOLO al rol dueño (el de migraciones, que ya podía desactivar RLS), para que las
    # funciones SECURITY DEFINER puedan operar. El rol de la app no cambia. Ver ADR 004.
    op.execute("CREATE POLICY users_duenio ON users TO CURRENT_USER USING (true) WITH CHECK (true)")

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_hash", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.execute("ALTER TABLE sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sessions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY sessions_propio ON sessions
            USING (user_id = app_current_user_id())
            WITH CHECK (user_id = app_current_user_id())
        """
    )
    op.execute(
        "CREATE POLICY sessions_duenio ON sessions TO CURRENT_USER USING (true) WITH CHECK (true)"
    )

    # Estado del flujo OAuth antes de saber quién es el usuario: sin datos de usuario.
    op.create_table(
        "oauth_transactions",
        sa.Column("state_hash", sa.LargeBinary(), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state_hash", name=op.f("pk_oauth_transactions")),
    )

    # Alta o actualización del usuario que acaba de autenticarse con OAuth. Devuelve solo
    # el id. El rol admin se recalcula en cada ingreso (si sale de ADMIN_EMAILS, lo pierde);
    # el plan se asigna solo al crear el usuario.
    op.execute(
        """
        CREATE FUNCTION upsert_usuario_oauth(
            p_proveedor text, p_sub text, p_email text, p_nombre text,
            p_plan_nuevo text, p_es_admin boolean
        ) RETURNS uuid
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
        DECLARE
            v_id uuid;
        BEGIN
            UPDATE users
               SET email = p_email,
                   nombre = p_nombre,
                   rol = CASE WHEN p_es_admin THEN 'admin'
                              WHEN rol = 'admin' THEN 'usuario'
                              ELSE rol END
             WHERE proveedor_oauth = p_proveedor AND sub_oauth = p_sub
            RETURNING id INTO v_id;

            IF v_id IS NULL THEN
                INSERT INTO users (email, nombre, proveedor_oauth, sub_oauth, plan, rol)
                VALUES (p_email, p_nombre, p_proveedor, p_sub, p_plan_nuevo,
                        CASE WHEN p_es_admin THEN 'admin' ELSE 'usuario' END)
                RETURNING id INTO v_id;
            END IF;
            RETURN v_id;
        END
        $$
        """
    )

    # Sesión vigente para un hash de token: solo id, rol y plan del usuario.
    op.execute(
        """
        CREATE FUNCTION resolver_sesion(p_token_hash bytea)
            RETURNS TABLE (user_id uuid, rol text, plan text)
            LANGUAGE sql STABLE SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
            SELECT s.user_id, u.rol, u.plan
              FROM sessions s
              JOIN users u ON u.id = s.user_id
             WHERE s.token_hash = p_token_hash
               AND s.expira_en > now()
               AND u.borrado_en IS NULL
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION resolver_sesion(bytea)")
    op.execute("DROP FUNCTION upsert_usuario_oauth(text, text, text, text, text, boolean)")
    op.drop_table("oauth_transactions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.execute("DROP POLICY users_duenio ON users")
