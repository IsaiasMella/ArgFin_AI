"""`SqlCallRecorder` contra PostgreSQL con el rol de la app (T0.5)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from brujula.core.llm.records import LLMCallRecord, SqlCallRecorder
from tests.db.conftest import Database

pytestmark = pytest.mark.anyio


def _call(cost: str | None, success: bool = True) -> LLMCallRecord:
    return LLMCallRecord(
        purpose="clasificacion",
        model="anthropic/claude-haiku-4-5",
        prompt_ref="clasificar_noticia@1",
        attempt=1,
        tokens_in=100,
        tokens_out=20,
        cost_usd=Decimal(cost) if cost is not None else None,
        latency_ms=850,
        success=success,
        error=None if success else "RateLimitError",
        trace_id="trace-1",
    )


@pytest.fixture
async def app_engine(database: Database) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database.url("app"))
    yield engine
    await engine.dispose()
    with psycopg.connect(database.conninfo("superuser"), autocommit=True) as conn:
        conn.execute("DELETE FROM llm_calls")


async def test_registra_llamadas_y_suma_el_gasto(app_engine: AsyncEngine) -> None:
    recorder = SqlCallRecorder(app_engine)
    before = datetime.now(UTC) - timedelta(seconds=5)

    await recorder.record(_call("0.012500"))
    await recorder.record(_call("0.003000"))
    await recorder.record(_call(None, success=False))  # falla sin respuesta: sin costo

    assert await recorder.cost_since(before) == Decimal("0.0155")
    assert await recorder.cost_since(datetime.now(UTC) + timedelta(minutes=1)) == Decimal(0)
