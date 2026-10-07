"""Carga del portafolio por CSV de punta a punta (T1.4)."""

from typing import Any

import psycopg
from fastapi.testclient import TestClient

from brujula.core.config import Settings
from tests.db.conftest import make_client
from tests.support.app import csrf_headers, login
from tests.support.google import FakeGoogle

CSV = (
    "ticker,cantidad,precio_promedio,moneda_precio\n"
    "GGAL,100,4200.5,ARS\n"
    "MAL TICKER,1,,\n"
    "YPFD,25,,\n"
)


def upload(client: TestClient, content: str) -> Any:
    return client.post(
        "/portfolio/csv",
        files={"archivo": ("portafolio.csv", content.encode(), "text/csv")},
        headers=csrf_headers(client),
    )


def test_plantilla_descargable(client: TestClient) -> None:
    response = client.get("/portfolio/csv/plantilla")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.startswith("ticker,cantidad")


def test_guarda_las_filas_validas_e_informa_las_invalidas(
    client: TestClient, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    login(client, google)

    response = upload(client, CSV)

    assert response.status_code == 200
    body = response.json()
    assert body["filas_procesadas"] == 3
    assert [h["ticker"] for h in body["importadas"]] == ["GGAL", "YPFD"]
    [error] = body["errores"]
    assert error["fila"] == 3
    assert error["errores"][0].startswith("ticker: ticker inválido")
    # La fila inválida no se guardó.
    assert superuser.execute("SELECT count(*) FROM holdings").fetchone() == (2,)


def test_archivo_invalido_no_guarda_nada(
    client: TestClient, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    login(client, google)

    response = upload(client, "simbolo,cantidad\nGGAL,1\n")

    assert response.status_code == 400
    assert "faltan: ticker" in response.json()["detail"]
    assert superuser.execute("SELECT count(*) FROM holdings").fetchone() == (0,)


def test_archivo_demasiado_grande(
    auth_settings: Settings, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    small = auth_settings.model_copy(update={"csv_max_bytes": 30})
    with make_client(small, google) as client:
        login(client, google)
        response = upload(client, CSV)

    assert response.status_code == 400
    assert "máximo de 30 bytes" in response.json()["detail"]


def test_respeta_el_limite_del_plan_gratuito(
    auth_settings: Settings, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    free = auth_settings.model_copy(
        update={"founder_plan_open": False, "free_plan_max_positions": 1}
    )
    with make_client(free, google) as client:
        login(client, google)
        body = upload(client, CSV).json()

    assert [h["ticker"] for h in body["importadas"]] == ["GGAL"]
    errors = {e["fila"]: e["errores"] for e in body["errores"]}
    assert "supera el límite del plan" in errors[4][0]


def test_rate_limit_de_cargas(
    auth_settings: Settings, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    limited = auth_settings.model_copy(update={"rate_limit_upload_per_hour": 1})
    with make_client(limited, google) as client:
        login(client, google)
        codes = [upload(client, "ticker,cantidad\n").status_code for _ in range(2)]

    assert codes == [200, 429]


def test_requiere_csrf(client: TestClient, google: FakeGoogle) -> None:
    login(client, google)

    response = client.post(
        "/portfolio/csv", files={"archivo": ("p.csv", b"ticker,cantidad\n", "text/csv")}
    )

    assert response.status_code == 403
