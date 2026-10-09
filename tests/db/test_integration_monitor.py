"""Monitor de integraciones contra la base: cada tipo de falla, un único aviso (T3.7)."""

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import psycopg
import pytest

from brujula.core.config import Settings
from brujula.core.db import create_engine, create_session_factory
from brujula.core.http import FetchError
from brujula.core.integrations import SourceRun
from brujula.features.integrations.reporting import report_runs
from brujula.features.integrations.service import IntegrationMonitor, load_monitor_config

pytestmark = pytest.mark.anyio

CONFIG = Path(__file__).resolve().parents[2] / "config"
T0 = datetime(2026, 10, 8, 12, tzinfo=UTC)
ADMINS = ["jefa@ejemplo.com"]


@dataclass
class FakeSender:
    sent: list[dict[str, Any]] = field(default_factory=list)
    down: bool = False

    async def send(
        self, *, to: Sequence[str], subject: str, text: str, idempotency_key: str
    ) -> None:
        if self.down:
            raise FetchError("http_503")
        self.sent.append({"to": list(to), "subject": subject, "text": text})


@pytest.fixture
async def monitor(auth_settings: Settings) -> AsyncIterator[IntegrationMonitor]:
    engine = create_engine(auth_settings)
    yield IntegrationMonitor(create_session_factory(engine), load_monitor_config(CONFIG))
    await engine.dispose()


def at(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


def incidents(conn: psycopg.Connection) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT tipo, fuente, objeto, avisado_en IS NOT NULL, cerrado_en IS NOT NULL"
        " FROM integration_incidents ORDER BY abierto_en, tipo"
    ).fetchall()


async def test_falla_repetida_se_avisa_una_sola_vez(
    monitor: IntegrationMonitor, superuser: psycopg.Connection
) -> None:
    sender = FakeSender()
    down = SourceRun("sitio", "TRANSENER", "http_503")

    for hour in (0, 6):  # dos fallas: puede ser algo pasajero
        await monitor.record([down], at(hour))
        assert await monitor.notify(sender, ADMINS, at(hour)) == 0
    await monitor.record([down], at(12))  # la tercera seguida
    assert await monitor.notify(sender, ADMINS, at(12)) == 1
    await monitor.record([down], at(18))  # sigue caída: no se vuelve a avisar
    assert await monitor.notify(sender, ADMINS, at(18)) == 0

    [email] = sender.sent
    assert email["to"] == ADMINS
    assert "Falla repetida: sitio / TRANSENER" in email["text"]
    assert "3 corridas seguidas con error (último motivo: http_503)" in email["text"]
    assert incidents(superuser) == [("falla_repetida", "sitio", "TRANSENER", True, False)]


async def test_vuelve_a_andar_se_cierra_y_si_falla_de_nuevo_es_otro_aviso(
    monitor: IntegrationMonitor, superuser: psycopg.Connection
) -> None:
    sender = FakeSender()
    down = SourceRun("cnv", "YPF", "sin_conexion")
    for hour in range(3):
        await monitor.record([down], at(hour))
    await monitor.notify(sender, ADMINS, at(3))

    await monitor.record([SourceRun("cnv", "YPF")], at(4))
    assert incidents(superuser) == [("falla_repetida", "cnv", "YPF", True, True)]

    for hour in range(5, 8):
        await monitor.record([down], at(hour))
    assert await monitor.notify(sender, ADMINS, at(8)) == 1
    assert len(sender.sent) == 2


async def test_una_falla_intercalada_no_suma_para_la_racha(
    monitor: IntegrationMonitor, superuser: psycopg.Connection
) -> None:
    down = SourceRun("sec", "GGAL", "http_500")
    for hour, run in enumerate([down, down, SourceRun("sec", "GGAL"), down, down]):
        await monitor.record([run], at(hour))

    assert incidents(superuser) == []


async def test_las_fallas_se_cuentan_por_empresa(
    monitor: IntegrationMonitor, superuser: psycopg.Connection
) -> None:
    for hour, company in enumerate(["YPF", "GGAL", "PAMPA"]):
        await monitor.record([SourceRun("cnv", company, "http_500")], at(hour))

    assert incidents(superuser) == []


async def test_cambio_de_formato_se_avisa_a_la_primera_y_una_sola_vez(
    monitor: IntegrationMonitor, superuser: psycopg.Connection
) -> None:
    sender = FakeSender()
    changed = SourceRun("sitio", "BYMA", "wordpress_formato_inesperado", formato=True)

    await monitor.record([changed], at(0))
    assert await monitor.notify(sender, ADMINS, at(0)) == 1
    await monitor.record([changed], at(6))
    assert await monitor.notify(sender, ADMINS, at(6)) == 0

    [email] = sender.sent
    assert "Cambio de formato: sitio / BYMA" in email["text"]
    assert "formato inesperado (wordpress_formato_inesperado)" in email["text"]


async def test_estado_contable_vencido_y_no_presentado(
    monitor: IntegrationMonitor, superuser: psycopg.Connection
) -> None:
    sender = FakeSender()
    fiscal_ends = {"YPF": "12-31", "ALUAR": "06-30"}
    # El 8/10/2026 ya venció el trimestral al 30/06 (42 días + 5 de margen) de YPF. El anual
    # de Aluar al 30/06 vence a los 70 días: el 8/9, también vencido.
    published = {"YPF": {date(2026, 3, 31)}, "ALUAR": {date(2026, 6, 30)}}

    await monitor.check_statements(fiscal_ends, published, date(2026, 10, 8), at(0))
    assert await monitor.notify(sender, ADMINS, at(0)) == 1
    await monitor.check_statements(fiscal_ends, published, date(2026, 10, 9), at(24))
    assert await monitor.notify(sender, ADMINS, at(24)) == 0

    [email] = sender.sent
    assert "Documento no publicado: cnv / YPF 2026-06-30" in email["text"]
    assert "trimestral con cierre 2026-06-30 (vencía el 2026-08-11)" in email["text"]

    published["YPF"].add(date(2026, 6, 30))  # la empresa presentó tarde
    await monitor.check_statements(fiscal_ends, published, date(2026, 10, 10), at(48))
    assert incidents(superuser) == [("documento_faltante", "cnv", "YPF 2026-06-30", True, True)]


async def test_dentro_del_plazo_no_se_avisa(
    monitor: IntegrationMonitor, superuser: psycopg.Connection
) -> None:
    # El 15/8/2026 todavía no venció el trimestral al 30/06 (42 días + 5 de margen).
    published = {"YPF": {date(2026, 3, 31)}}

    await monitor.check_statements({"YPF": "12-31"}, published, date(2026, 8, 15), at(0))

    assert incidents(superuser) == []


async def test_si_el_email_falla_se_reintenta_sin_duplicar(
    monitor: IntegrationMonitor, superuser: psycopg.Connection
) -> None:
    sender = FakeSender(down=True)
    await monitor.record([SourceRun("sec_xbrl", "AAPL", "sec_formato_inesperado", True)], at(0))

    assert await monitor.notify(sender, ADMINS, at(0)) == 0
    sender.down = False
    assert await monitor.notify(sender, ADMINS, at(1)) == 1
    assert await monitor.notify(sender, ADMINS, at(2)) == 0
    assert len(sender.sent) == 1


async def test_varios_incidentes_nuevos_van_en_un_solo_email(
    monitor: IntegrationMonitor, superuser: psycopg.Connection
) -> None:
    sender = FakeSender()
    await monitor.record(
        [
            SourceRun("sitio", "BYMA", "wordpress_formato_inesperado", formato=True),
            SourceRun("precios:byma", "", "formato_inesperado", formato=True),
            SourceRun("cnv", "GGAL"),
        ],
        at(0),
    )

    assert await monitor.notify(sender, ADMINS, at(0)) == 2
    [email] = sender.sent
    assert email["subject"] == "Brújula: 2 incidente(s) en las integraciones"
    assert "Cambio de formato: precios:byma\n" in email["text"]


async def test_las_corridas_viejas_se_borran(
    monitor: IntegrationMonitor, superuser: psycopg.Connection
) -> None:
    await monitor.record([SourceRun("cnv", "YPF")], at(0))
    await monitor.record([SourceRun("cnv", "YPF")], at(24 * 91))

    assert superuser.execute("SELECT count(*) FROM integration_runs").fetchone() == (1,)


async def test_report_runs_avisa_por_resend_solo_a_los_administradores(
    auth_settings: Settings, superuser: psycopg.Connection
) -> None:
    requests: list[httpx2.Request] = []

    def resend(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"id": "email-1"})

    sent = await report_runs(
        auth_settings,
        [SourceRun("sitio", "TGN", "wordpress_formato_inesperado", formato=True)],
        now=T0,
        http_transport=httpx2.MockTransport(resend),
    )

    assert sent == 1
    [request] = requests
    assert str(request.url) == "https://resend.example.invalid/emails"
    assert request.headers["Authorization"] == "Bearer resend-key"
    assert len(request.headers["Idempotency-Key"]) == 64
    body = json.loads(request.content)
    assert body["to"] == ["jefa@ejemplo.com"]  # ADMIN_EMAILS, nunca clientes
    assert body["from"] == auth_settings.email_from
    assert "Cambio de formato: sitio / TGN" in body["text"]
