"""Corridas contra fuentes externas e incidentes del monitor de integraciones (T3.7)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from brujula.core.db import Base

INCIDENT_TYPES = ("falla_repetida", "formato", "documento_faltante")


class IntegrationRun(Base):
    __tablename__ = "integration_runs"
    __table_args__ = (Index("ix_integration_runs_fuente_objeto", "fuente", "objeto", "creado_en"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    fuente: Mapped[str] = mapped_column(Text)
    # Empresa o proveedor consultado ("" si la corrida abarca toda la fuente).
    objeto: Mapped[str] = mapped_column(Text)
    ok: Mapped[bool]
    motivo: Mapped[str | None] = mapped_column(Text)
    formato: Mapped[bool]
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntegrationIncident(Base):
    """Un problema con una fuente, desde que se detecta hasta que se resuelve.

    Se avisa una sola vez por incidente (`avisado_en`). Mientras está abierto no se abre otro
    igual; si se resuelve y vuelve a pasar, es un incidente nuevo.
    """

    __tablename__ = "integration_incidents"
    __table_args__ = (
        CheckConstraint("tipo IN ('falla_repetida', 'formato', 'documento_faltante')", name="tipo"),
        Index(
            "uq_integration_incidents_abierto",
            "tipo",
            "fuente",
            "objeto",
            unique=True,
            postgresql_where=text("cerrado_en IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    tipo: Mapped[str] = mapped_column(Text)
    fuente: Mapped[str] = mapped_column(Text)
    objeto: Mapped[str] = mapped_column(Text)
    detalle: Mapped[str] = mapped_column(Text)
    abierto_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    avisado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cerrado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
