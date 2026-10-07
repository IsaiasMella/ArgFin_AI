"""Posiciones del portafolio. Tabla de usuario: RLS y cantidad/precio cifrados (HU-02)."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from brujula.core.db import Base
from brujula.core.security.encrypted_types import EncryptedDecimal

QUANTITY_CONTEXT = "holdings.cantidad"
PRICE_CONTEXT = "holdings.precio_promedio"


class Holding(Base):
    __tablename__ = "holdings"
    __table_args__ = (
        CheckConstraint(
            "(instrument_id IS NULL) <> (ticker_libre IS NULL)", name="instrumento_o_ticker"
        ),
        CheckConstraint(
            "(precio_promedio_cifrado IS NULL) = (moneda_precio IS NULL)",
            name="precio_con_moneda",
        ),
        CheckConstraint("moneda_precio IN ('ARS', 'USD')", name="moneda_precio"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[UUID | None] = mapped_column(ForeignKey("instruments.id"))
    ticker_libre: Mapped[str | None] = mapped_column(Text)
    cantidad: Mapped[Decimal] = mapped_column(
        "cantidad_cifrada", EncryptedDecimal(QUANTITY_CONTEXT)
    )
    precio_promedio: Mapped[Decimal | None] = mapped_column(
        "precio_promedio_cifrado", EncryptedDecimal(PRICE_CONTEXT)
    )
    moneda_precio: Mapped[str | None] = mapped_column(Text)
    broker: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
