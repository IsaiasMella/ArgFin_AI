"""Borrado real de la cuenta y sus datos (T1.5 / HU-08)."""

import psycopg
from fastapi.testclient import TestClient

from brujula.core.config import Settings
from brujula.features.auth.cookies import SESSION_COOKIE
from tests.db.conftest import Database, make_client
from tests.support.app import csrf_headers, login
from tests.support.google import FakeGoogle

USER_TABLES_COUNT = (
    "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM sessions),"
    " (SELECT count(*) FROM holdings)"
)


def add_holding(client: TestClient, ticker: str) -> None:
    response = client.post(
        "/portfolio/holdings",
        json={"ticker": ticker, "cantidad": "10"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 201


def delete_account(client: TestClient, email: str) -> int:
    response = client.request(
        "DELETE", "/me", json={"confirmacion_email": email}, headers=csrf_headers(client)
    )
    return response.status_code


def test_borra_el_usuario_y_todos_sus_datos(
    client: TestClient, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    login(client, google)
    add_holding(client, "GGAL")
    add_holding(client, "YPFD")
    old_token = client.cookies[SESSION_COOKIE]

    assert delete_account(client, " Ana@Ejemplo.com ") == 204

    assert superuser.execute(USER_TABLES_COUNT).fetchone() == (0, 0, 0)
    assert SESSION_COOKIE not in client.cookies  # la respuesta borró las cookies
    client.cookies.set(SESSION_COOKIE, old_token, domain="testserver")
    assert client.get("/me").status_code == 401


def test_no_toca_los_datos_de_otros_usuarios(
    auth_settings: Settings, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    with make_client(auth_settings, google) as ana, make_client(auth_settings, google) as beto:
        login(ana, google)
        add_holding(ana, "GGAL")
        login(beto, google, sub="sub-beto", email="beto@ejemplo.com")
        add_holding(beto, "YPFD")

        assert delete_account(ana, "ana@ejemplo.com") == 204

        assert [h["ticker"] for h in beto.get("/portfolio/holdings").json()] == ["YPFD"]
    assert superuser.execute(USER_TABLES_COUNT).fetchone() == (1, 1, 1)


def test_confirmacion_incorrecta_no_borra(
    client: TestClient, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    login(client, google)

    assert delete_account(client, "otra@ejemplo.com") == 422
    assert client.get("/me").status_code == 200


def test_requiere_csrf(client: TestClient, google: FakeGoogle) -> None:
    login(client, google)

    response = client.request("DELETE", "/me", json={"confirmacion_email": "ana@ejemplo.com"})

    assert response.status_code == 403


def test_volver_a_ingresar_crea_una_cuenta_nueva(
    client: TestClient, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    login(client, google)
    first_id = client.get("/me").json()["id"]
    delete_account(client, "ana@ejemplo.com")

    login(client, google)

    assert client.get("/me").json()["id"] != first_id
    assert client.get("/portfolio/holdings").json() == []


# --- Guardianes: toda tabla de usuario, presente o futura, cumple las reglas del ADR 004 ---

USER_TABLES_QUERY = """
    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
           EXISTS (
               SELECT 1 FROM pg_constraint fk
                WHERE fk.conrelid = c.oid AND fk.contype = 'f'
                  AND fk.confrelid = 'users'::regclass AND fk.confdeltype = 'c'
           ) AS cascada
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'user_id' AND NOT a.attisdropped
     WHERE n.nspname = 'public' AND c.relkind = 'r'
     ORDER BY c.relname
"""


def test_toda_tabla_de_usuario_tiene_rls_forzado_y_borrado_en_cascada(database: Database) -> None:
    with psycopg.connect(database.conninfo("migrator")) as conn:
        tables = conn.execute(USER_TABLES_QUERY).fetchall()

    assert {name for name, *_ in tables} >= {"sessions", "holdings"}
    offenders = [name for name, rls, forced, cascade in tables if not (rls and forced and cascade)]
    assert offenders == [], f"tablas de usuario sin RLS forzado o sin cascada: {offenders}"
