"""Empresas e instrumentos del universo cubierto (tablas compartidas, sin datos de usuario)."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from brujula.core.db import Base


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (CheckConstraint("tipo IN ('ar_equity', 'cedear')", name="tipo"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    nombre: Mapped[str] = mapped_column(Text)
    sector: Mapped[str] = mapped_column(Text)
    pais: Mapped[str] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(Text)
    cik_sec: Mapped[str | None] = mapped_column(Text)
    url_relacion_inversores: Mapped[str | None] = mapped_column(Text)
    activa: Mapped[bool] = mapped_column(server_default=text("true"))


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"))
    ticker_byma: Mapped[str] = mapped_column(Text, unique=True)
    ticker_origen: Mapped[str | None] = mapped_column(Text)
    ratio_cedear: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    moneda: Mapped[str] = mapped_column(Text)
