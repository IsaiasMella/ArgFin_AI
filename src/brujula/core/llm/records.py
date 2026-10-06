"""Registro de cada llamada a un LLM en `llm_calls` (tabla compartida, sin datos de usuario).

Se registra **cada intento**, también los fallidos: un intento con respuesta inválida
igual se paga, y el tope mensual se calcula sumando lo registrado acá.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, Numeric, Text, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Mapped, mapped_column

from brujula.core.db import Base


class LLMCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_creado_en", "creado_en"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    proposito: Mapped[str] = mapped_column(Text)
    modelo: Mapped[str] = mapped_column(Text)
    version_prompt: Mapped[str] = mapped_column(Text)
    intento: Mapped[int] = mapped_column(Integer)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    costo_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latencia_ms: Mapped[int] = mapped_column(Integer)
    exito: Mapped[bool]
    error: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(Text)


@dataclass(frozen=True)
class LLMCallRecord:
    purpose: str
    model: str
    prompt_ref: str
    attempt: int
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: Decimal | None
    latency_ms: int
    success: bool
    error: str | None
    trace_id: str | None


class CallRecorder(Protocol):
    async def record(self, call: LLMCallRecord) -> None: ...

    async def cost_since(self, since: datetime) -> Decimal: ...


class SqlCallRecorder:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record(self, call: LLMCallRecord) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                insert(LLMCall).values(
                    proposito=call.purpose,
                    modelo=call.model,
                    version_prompt=call.prompt_ref,
                    intento=call.attempt,
                    tokens_in=call.tokens_in,
                    tokens_out=call.tokens_out,
                    costo_usd=call.cost_usd,
                    latencia_ms=call.latency_ms,
                    exito=call.success,
                    error=call.error,
                    trace_id=call.trace_id,
                )
            )

    async def cost_since(self, since: datetime) -> Decimal:
        query = select(func.coalesce(func.sum(LLMCall.costo_usd), 0)).where(
            LLMCall.creado_en >= since
        )
        async with self._engine.connect() as connection:
            total = await connection.scalar(query)
        return Decimal(total or 0)
