"""Documentos descargados y datos estructurados de la CNV (tablas compartidas)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from brujula.core.db import Base

DOCUMENT_TYPES = (
    "estado_contable",
    "resena_informativa",
    "memoria_anual",
    "comunicado_resultados",
)
SOURCES = ("cnv", "sec", "sitio_inversores")
# descargado → (T3.5) validado | revision_manual; error: no se pudo procesar.
STATES = ("descargado", "validado", "revision_manual", "error")


def sql_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(sql_in("tipo", DOCUMENT_TYPES), name="tipo"),
        CheckConstraint(sql_in("fuente", SOURCES), name="fuente"),
        CheckConstraint(sql_in("estado", STATES), name="estado"),
        # Cada documento de cada fuente se descarga una sola vez.
        UniqueConstraint("fuente", "clave_externa", name="fuente_clave_externa"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    tipo: Mapped[str] = mapped_column(Text)
    fuente: Mapped[str] = mapped_column(Text)
    clave_externa: Mapped[str] = mapped_column(Text)
    # Fecha de cierre del período al que se refiere (si se conoce).
    periodo: Mapped[date | None]
    url_origen: Mapped[str] = mapped_column(Text)
    nombre_archivo: Mapped[str] = mapped_column(Text)
    tipo_contenido: Mapped[str] = mapped_column(Text)
    hash_sha256: Mapped[str] = mapped_column(Text, index=True)
    tamano_bytes: Mapped[int]
    ruta_almacenada: Mapped[str] = mapped_column(Text)
    fecha_publicacion: Mapped[date | None]
    estado: Mapped[str] = mapped_column(Text, server_default=text("'descargado'"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Verificación triple (T3.5): cuándo se hizo y cuánto costó el LLM.
    verificado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    costo_verificacion_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))


class CnvStatement(Base):
    """Estado contable tal como la empresa lo declaró en el formulario de la CNV (AIF)."""

    __tablename__ = "cnv_statements"
    __table_args__ = (
        CheckConstraint("tipo_balance IN ('consolidado', 'individual')", name="tipo_balance"),
        CheckConstraint("periodicidad IN ('trimestral', 'anual')", name="periodicidad"),
    )

    presentacion_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    fecha_cierre: Mapped[date]
    periodicidad: Mapped[str] = mapped_column(Text)
    tipo_balance: Mapped[str] = mapped_column(Text)
    moneda: Mapped[str | None] = mapped_column(Text)
    unidad: Mapped[str | None] = mapped_column(Text)
    norma_contable: Mapped[str | None] = mapped_column(Text)
    # [{"nro": "1999999", "rubro": "TOTAL DEL ACTIVO", "monto": "9388201890.00"}], tal cual.
    cuentas: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    url_origen: Mapped[str] = mapped_column(Text)
    fecha_publicacion: Mapped[date | None]
    # El PDF del estado contable de esta presentación (para la verificación de T3.5).
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentCheck(Base):
    """Ítems de una fuente ya revisados que no eran documentos de interés (p. ej. un 6-K que
    no es un comunicado de resultados), para no volver a descargarlos en cada corrida."""

    __tablename__ = "document_checks"

    fuente: Mapped[str] = mapped_column(Text, primary_key=True)
    clave_externa: Mapped[str] = mapped_column(Text, primary_key=True)
    resultado: Mapped[str] = mapped_column(Text)
    revisado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
