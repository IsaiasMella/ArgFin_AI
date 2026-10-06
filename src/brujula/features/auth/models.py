"""Modelos de identidad. `users` es una tabla de usuario: tiene RLS (migración 0001)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from brujula.core.db import Base

PLANS = ("gratis", "pro", "fundador")


class Role(Base):
    """Catálogo de roles (permisos). Extensible sin migrar enums."""

    __tablename__ = "roles"

    codigo: Mapped[str] = mapped_column(Text, primary_key=True)
    descripcion: Mapped[str] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("proveedor_oauth", "sub_oauth", name="uq_users_proveedor_sub"),
        CheckConstraint(f"plan IN {PLANS!r}", name="plan_valido"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    email: Mapped[str] = mapped_column(Text)
    nombre: Mapped[str | None] = mapped_column(Text)
    proveedor_oauth: Mapped[str] = mapped_column(Text)
    sub_oauth: Mapped[str] = mapped_column(Text)
    rol: Mapped[str] = mapped_column(ForeignKey("roles.codigo"), server_default=text("'usuario'"))
    plan: Mapped[str] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    borrado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
