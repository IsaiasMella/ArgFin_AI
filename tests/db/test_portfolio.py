"""Portafolio: alta, edición, baja y listado con RLS y validación contra el universo (T1.3)."""

from decimal import Decimal
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from brujula.core.config import Settings
from tests.db.conftest import make_client
from tests.support.app import csrf_headers, login
from tests.support.google import FakeGoogle

GGAL = {"ticker": "ggal", "cantidad": "150", "precio_promedio": "4200.50", "moneda_precio": "ARS"}


def seed_universe(conn: psycopg.Connection, ticker: str = "GGAL", active: bool = True) -> None:
    company_id = conn.execute(
        "INSERT INTO companies (nombre, sector, pais, tipo, activa)"
        " VALUES (%s, 'Bancos', 'AR', 'ar_equity', %s) RETURNING id",
        (f"Empresa {ticker}", active),
    ).fetchone()
    assert company_id is not None
    conn.execute(
        "INSERT INTO instruments (company_id, ticker_byma, moneda) VALUES (%s, %s, 'ARS')",
        (company_id[0], ticker),
    )


def add(client: TestClient, payload: dict[str, Any]) -> Any:
    return client.post("/portfolio/holdings", json=payload, headers=csrf_headers(client))


def test_alta_indica_cobertura_completa_o_solo_precio(
    client: TestClient, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    seed_universe(superuser, "GGAL")
    login(client, google)

    covered = add(client, GGAL)
    price_only = add(client, {"ticker": " zzz9 ", "cantidad": "3"})

    assert covered.status_code == 201
    assert covered.json() | {"id": None} == {
        "id": None,
        "ticker": "GGAL",
        "empresa": "Empresa GGAL",
        "cobertura": "completa",
        "cantidad": "150",
        "precio_promedio": "4200.50",
        "moneda_precio": "ARS",
        "broker": None,
    }
    assert price_only.json()["ticker"] == "ZZZ9"
    assert price_only.json()["cobertura"] == "solo_precio"


def test_empresa_inactiva_queda_solo_precio(
    client: TestClient, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    seed_universe(superuser, "XYZ", active=False)
    login(client, google)

    assert add(client, {"ticker": "XYZ", "cantidad": "1"}).json()["cobertura"] == "solo_precio"


def test_ticker_libre_pasa_a_completo_si_entra_al_universo(
    client: TestClient, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    login(client, google)
    add(client, {"ticker": "NUEVO", "cantidad": "10"})

    seed_universe(superuser, "NUEVO")

    [holding] = client.get("/portfolio/holdings").json()
    assert holding["cobertura"] == "completa"


def test_listar_editar_y_borrar(client: TestClient, google: FakeGoogle) -> None:
    login(client, google)
    holding_id = add(client, GGAL).json()["id"]

    edited = client.patch(
        f"/portfolio/holdings/{holding_id}",
        json={"cantidad": "175.5", "broker": "  Mi Broker  "},
        headers=csrf_headers(client),
    )
    assert edited.status_code == 200
    assert (edited.json()["cantidad"], edited.json()["broker"]) == ("175.5", "Mi Broker")
    assert edited.json()["precio_promedio"] == "4200.50"  # lo no enviado no cambia

    removed_price = client.patch(
        f"/portfolio/holdings/{holding_id}",
        json={"precio_promedio": None, "moneda_precio": None},
        headers=csrf_headers(client),
    )
    assert removed_price.json()["precio_promedio"] is None

    deleted = client.delete(f"/portfolio/holdings/{holding_id}", headers=csrf_headers(client))
    assert deleted.status_code == 204
    assert client.get("/portfolio/holdings").json() == []


def test_cantidad_y_precio_cifrados_en_la_base(
    client: TestClient, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    login(client, google)
    add(client, {**GGAL, "cantidad": "987654", "precio_promedio": "123456.78"})

    row = superuser.execute(
        "SELECT cantidad_cifrada, precio_promedio_cifrado FROM holdings"
    ).fetchone()
    assert row is not None
    for blob, plain in zip(row, (b"987654", b"123456.78"), strict=True):
        assert isinstance(blob, bytes)
        assert plain not in blob


def test_aislamiento_entre_usuarios(
    auth_settings: Settings, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    with make_client(auth_settings, google) as ana, make_client(auth_settings, google) as beto:
        login(ana, google)
        holding_id = add(ana, GGAL).json()["id"]
        login(beto, google, sub="sub-beto", email="beto@ejemplo.com")

        assert beto.get("/portfolio/holdings").json() == []
        update = beto.patch(
            f"/portfolio/holdings/{holding_id}",
            json={"cantidad": "1"},
            headers=csrf_headers(beto),
        )
        assert update.status_code == 404
        delete = beto.delete(f"/portfolio/holdings/{holding_id}", headers=csrf_headers(beto))
        assert delete.status_code == 404

        [still_there] = ana.get("/portfolio/holdings").json()
        assert Decimal(still_there["cantidad"]) == Decimal(150)


def test_limite_del_plan_gratuito(
    auth_settings: Settings, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    free = auth_settings.model_copy(
        update={"founder_plan_open": False, "free_plan_max_positions": 2}
    )
    with make_client(free, google) as client:
        login(client, google)
        codes = [add(client, {"ticker": t, "cantidad": "1"}).status_code for t in "ABC"]
        blocked = add(client, {"ticker": "D", "cantidad": "1"})

    assert codes == [201, 201, 403]
    assert "hasta 2 posiciones" in blocked.json()["detail"]


def test_plan_fundador_no_tiene_limite(
    auth_settings: Settings, google: FakeGoogle, superuser: psycopg.Connection
) -> None:
    founder = auth_settings.model_copy(update={"free_plan_max_positions": 1})
    with make_client(founder, google) as client:
        login(client, google)
        codes = [add(client, {"ticker": t, "cantidad": "1"}).status_code for t in "AB"]

    assert codes == [201, 201]


@pytest.mark.parametrize(
    "payload",
    [
        {"ticker": "GG AL", "cantidad": "1"},
        {"ticker": "", "cantidad": "1"},
        {"ticker": "GGAL", "cantidad": "0"},
        {"ticker": "GGAL", "cantidad": "-5"},
        {"ticker": "GGAL", "cantidad": "1", "precio_promedio": "10"},  # falta la moneda
        {"ticker": "GGAL", "cantidad": "1", "precio_promedio": "10", "moneda_precio": "EUR"},
        {"ticker": "GGAL", "cantidad": "1", "usuario_id": "otro"},  # campos de más
    ],
)
def test_validaciones_de_entrada(
    client: TestClient, google: FakeGoogle, payload: dict[str, Any]
) -> None:
    login(client, google)

    assert add(client, payload).status_code == 422


def test_requiere_sesion_y_csrf(client: TestClient, google: FakeGoogle) -> None:
    assert client.get("/portfolio/holdings").status_code == 401
    login(client, google)

    assert client.post("/portfolio/holdings", json=GGAL).status_code == 403
