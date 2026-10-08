"""Cifras financieras por métrica interna y período (tabla compartida, plan técnico §5).

Cada fila dice de dónde salió (`fuente` y `referencia`) para que cualquier número de un
informe se pueda rastrear hasta el documento original (constitución, punto 2).
"""

from datetime import date, datetime
from decimal import Decimal
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
from sqlalchemy.orm import Mapped, mapped_column

from brujula.core.db import Base

FACT_SOURCES = ("sec_xbrl", "cnv")
MEASUREMENT_BASES = ("nominal", "homogenea")


class FinancialFact(Base):
    __tablename__ = "financial_facts"
    __table_args__ = (
        CheckConstraint("fuente IN ('sec_xbrl', 'cnv')", name="fuente"),
        CheckConstraint("base_medicion IN ('nominal', 'homogenea')", name="base_medicion"),
        CheckConstraint(
            "periodo_inicio IS NULL OR periodo_inicio <= periodo_fin", name="periodo_ordenado"
        ),
        # Un valor por métrica, período y fuente (el último presentado gana).
        UniqueConstraint(
            "company_id",
            "metrica",
            "periodo_inicio",
            "periodo_fin",
            "fuente",
            name="metrica_periodo_fuente",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id"))
    metrica: Mapped[str] = mapped_column(Text)
    # Nulo en los saldos (son a una fecha); en los flujos, el inicio del período.
    periodo_inicio: Mapped[date | None]
    periodo_fin: Mapped[date]
    valor: Mapped[Decimal] = mapped_column(Numeric(30, 6))
    # Código ISO de la moneda (USD, ARS); nulo para cantidades de acciones.
    moneda: Mapped[str | None] = mapped_column(Text)
    unidad: Mapped[str] = mapped_column(Text)
    base_medicion: Mapped[str] = mapped_column(Text)
    fecha_reexpresion: Mapped[date | None]
    es_comparativo: Mapped[bool] = mapped_column(server_default=text("false"))
    fuente: Mapped[str] = mapped_column(Text)
    # SEC: "<taxonomía>:<concepto> <accession>"; CNV: "presentación <id>, cuenta <nro>".
    referencia: Mapped[str] = mapped_column(Text)
    pagina: Mapped[int | None]
    formulario: Mapped[str | None] = mapped_column(Text)
    fecha_presentacion: Mapped[date | None]
    extractor_version: Mapped[str] = mapped_column(Text)
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class VerificationIssue(Base):
    """Una diferencia de la verificación triple: deja el documento en revisión manual."""

    __tablename__ = "verification_issues"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    metrica: Mapped[str | None] = mapped_column(Text)
    motivo: Mapped[str] = mapped_column(Text)
    detalle: Mapped[str] = mapped_column(Text)
    valor_cnv: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))
    valor_llm: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))
    pagina: Mapped[int | None]
    # Bloqueante: el documento quedó en revisión. No bloqueante: solo esa métrica no se publicó.
    bloqueante: Mapped[bool] = mapped_column(server_default=text("true"))
    modelo: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
