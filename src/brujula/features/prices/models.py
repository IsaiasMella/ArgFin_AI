"""Cierres diarios y tipo de cambio CCL, con su validación cruzada (tablas compartidas)."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from brujula.core.db import Base

SOURCES = ("byma", "data912")
_SOURCE_CHECK = "fuente IN ('byma', 'data912')"


class PriceDaily(Base):
    """Un cierre por instrumento y rueda. `marcado`: no se publica sin revisión (plan 7.1)."""

    __tablename__ = "prices_daily"
    __table_args__ = (
        CheckConstraint("cierre > 0", name="cierre_positivo"),
        CheckConstraint(_SOURCE_CHECK, name="fuente"),
        CheckConstraint("divergencia_pct >= 0", name="divergencia_no_negativa"),
    )

    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    fecha: Mapped[date] = mapped_column(primary_key=True)
    cierre: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    volumen: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    fuente: Mapped[str] = mapped_column(Text)
    # Cierre de la otra fuente y diferencia porcentual; nulos si solo una fuente tuvo la rueda.
    cierre_respaldo: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    divergencia_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    marcado: Mapped[bool] = mapped_column(server_default=text("false"))
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FxDaily(Base):
    """Dólar contado con liquidación (CCL) implícito en bonos, por rueda (ADR 012)."""

    __tablename__ = "fx_daily"
    __table_args__ = (
        CheckConstraint("ccl > 0", name="ccl_positivo"),
        CheckConstraint(_SOURCE_CHECK, name="fuente"),
        CheckConstraint("divergencia_pct >= 0", name="divergencia_no_negativa"),
    )

    fecha: Mapped[date] = mapped_column(primary_key=True)
    ccl: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    fuente: Mapped[str] = mapped_column(Text)
    ccl_respaldo: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    divergencia_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    marcado: Mapped[bool] = mapped_column(server_default=text("false"))
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
