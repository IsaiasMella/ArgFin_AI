"""Ingreso con Google de punta a punta contra PostgreSQL (T1.1 / HU-01)."""

import psycopg
import pytest
from fastapi.testclient import TestClient

from brujula.core.config import Settings
from brujula.core.security.hashing import token_hash
from brujula.features.auth.cookies import SESSION_COOKIE, STATE_COOKIE
from tests.db.conftest import Database, make_client
from tests.support.app import WEB_ORIGIN, csrf_headers, login
from tests.support.google import FakeGoogle


def test_ingreso_completo_crea_usuario_y_sesion(
    client: TestClient, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    login(client, google)

    profile = client.get("/me").json()
    assert profile["email"] == "ana@ejemplo.com"
    assert (profile["plan"], profile["rol"]) == ("fundador", "usuario")
    # En la base solo está el hash del token, nunca el token.
    token = client.cookies[SESSION_COOKIE]
    stored = superuser.execute("SELECT token_hash FROM sessions").fetchone()
    assert stored == (token_hash(token),)
    # PKCE: Google recibió el verificador que corresponde al challenge (lo valida el fake).
    assert len(google.token_requests) == 1


def test_cookies_de_sesion_seguras(
    auth_settings: Settings, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    with make_client(auth_settings, google) as client:
        start = client.get("/auth/google/login", follow_redirects=False)
        state_cookie = start.headers["set-cookie"].lower()
        assert all(flag in state_cookie for flag in ("httponly", "secure", "samesite=lax"))

        params = dict(
            pair.split("=", 1) for pair in start.headers["location"].split("?")[1].split("&")
        )
        google.next_claims = google.claims(nonce=params["nonce"])
        done = client.get(
            "/auth/google/callback",
            params={"code": "c", "state": client.cookies[STATE_COOKIE]},
            follow_redirects=False,
        )

    cookies = [c.lower() for c in done.headers.get_list("set-cookie")]
    session_cookie = next(c for c in cookies if c.startswith(SESSION_COOKIE))
    csrf_cookie = next(c for c in cookies if c.startswith("brujula_csrf="))
    assert all(flag in session_cookie for flag in ("httponly", "secure", "samesite=lax"))
    assert "httponly" not in csrf_cookie  # el frontend la tiene que poder leer
    assert done.headers["location"] == "http://localhost:3000"


def test_email_de_admin_recibe_rol_admin(client: TestClient, google: FakeGoogle) -> None:
    login(client, google, email="jefa@ejemplo.com", sub="sub-jefa")

    assert client.get("/me").json()["rol"] == "admin"


def test_reingreso_no_duplica_ni_cambia_el_plan(
    auth_settings: Settings, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    with make_client(auth_settings, google) as client:
        login(client, google)
    closed = auth_settings.model_copy(update={"founder_plan_open": False})
    with make_client(closed, google) as client:
        login(client, google, name="Ana Actualizada")
        profile = client.get("/me").json()

    assert profile["plan"] == "fundador"  # el plan se asigna solo al crear el usuario
    assert profile["nombre"] == "Ana Actualizada"
    assert superuser.execute("SELECT count(*) FROM users").fetchone() == (1,)


def test_state_que_no_coincide_se_rechaza_y_no_crea_sesion(
    client: TestClient, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    client.get("/auth/google/login", follow_redirects=False)
    google.next_claims = google.claims(nonce="cualquiera")

    response = client.get(
        "/auth/google/callback",
        params={"code": "c", "state": "state-de-un-atacante"},
        follow_redirects=False,
    )

    assert response.headers["location"].endswith("/ingresar?error=login_fallido")
    assert SESSION_COOKIE not in client.cookies
    assert google.token_requests == []  # ni siquiera se intentó canjear el código
    assert superuser.execute("SELECT count(*) FROM users").fetchone() == (0,)


def test_state_reutilizado_se_rechaza(client: TestClient, google: FakeGoogle) -> None:
    params = login(client, google)
    client.cookies.clear()
    client.cookies.set(STATE_COOKIE, params["state"], domain="testserver", path="/auth/google")

    replay = client.get(
        "/auth/google/callback",
        params={"code": "c", "state": params["state"]},
        follow_redirects=False,
    )

    assert replay.headers["location"].endswith("error=login_fallido")
    assert SESSION_COOKIE not in client.cookies


def test_token_invalido_se_rechaza(client: TestClient, google: FakeGoogle) -> None:
    start = client.get("/auth/google/login", follow_redirects=False)
    state = client.cookies[STATE_COOKIE]
    assert "nonce=" in start.headers["location"]
    google.next_claims = google.claims(nonce="nonce-que-no-es-el-del-flujo")

    response = client.get(
        "/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False
    )

    assert response.headers["location"].endswith("error=login_fallido")
    assert client.get("/me").status_code == 401


def test_error_del_proveedor_se_rechaza(client: TestClient) -> None:
    client.get("/auth/google/login", follow_redirects=False)

    response = client.get(
        "/auth/google/callback",
        params={"error": "access_denied", "state": client.cookies[STATE_COOKIE]},
        follow_redirects=False,
    )

    assert response.headers["location"].endswith("error=login_fallido")


def test_sesion_vencida_devuelve_401(
    client: TestClient, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    login(client, google)
    superuser.execute("UPDATE sessions SET expira_en = now() - interval '1 minute'")

    assert client.get("/me").status_code == 401


def test_sin_sesion_devuelve_401(client: TestClient) -> None:
    assert client.get("/me").status_code == 401


def test_logout_invalida_la_sesion_en_el_servidor(
    client: TestClient, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    login(client, google)
    old_token = client.cookies[SESSION_COOKIE]

    response = client.post("/auth/logout", headers=csrf_headers(client))

    assert response.status_code == 204
    assert superuser.execute("SELECT count(*) FROM sessions").fetchone() == (0,)
    client.cookies.set(SESSION_COOKIE, old_token, domain="testserver")
    assert client.get("/me").status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},  # sin token ni origen
        {"Origin": WEB_ORIGIN},  # sin token CSRF
        {"Origin": "https://sitio-malicioso.example", "X-CSRF-Token": "x"},
    ],
)
def test_logout_sin_csrf_valido_se_rechaza(
    client: TestClient, google: FakeGoogle, headers: dict[str, str]
) -> None:
    login(client, google)

    assert client.post("/auth/logout", headers=headers).status_code == 403
    assert client.get("/me").status_code == 200


def test_logout_con_token_csrf_de_otra_sesion_se_rechaza(
    client: TestClient, google: FakeGoogle
) -> None:
    login(client, google)
    headers = {"Origin": WEB_ORIGIN, "X-CSRF-Token": "0" * 64}

    assert client.post("/auth/logout", headers=headers).status_code == 403


def test_rate_limit_en_ingreso(
    auth_settings: Settings, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    limited = auth_settings.model_copy(update={"rate_limit_auth_per_minute": 2})
    with make_client(limited, google) as client:
        codes = [
            client.get("/auth/google/login", follow_redirects=False).status_code for _ in "abc"
        ]

    assert codes == [302, 302, 429]


def test_sesiones_aisladas_entre_usuarios(
    auth_settings: Settings, google: FakeGoogle, database: Database, superuser: psycopg.Connection
) -> None:
    with make_client(auth_settings, google) as client:
        login(client, google)
    with make_client(auth_settings, google) as client:
        login(client, google, sub="sub-beto", email="beto@ejemplo.com")

    ids = [row[0] for row in superuser.execute("SELECT id FROM users ORDER BY email")]
    with psycopg.connect(database.conninfo("app")) as conn:
        conn.execute("SELECT set_config('app.current_user_id', %s, true)", (str(ids[0]),))
        visible = conn.execute("SELECT DISTINCT user_id FROM sessions").fetchall()
    assert visible == [(ids[0],)]
