"""Monitor de integraciones (T3.7, plan 7.2 punto 6, ADR 019).

Cada fuente externa registra sus corridas (`SourceRun`). Se abre un incidente cuando:
- una fuente falla `fallas_consecutivas` veces seguidas para la misma empresa o proveedor;
- una fuente responde con un formato inesperado (posible cambio del sitio): a la primera;
- una empresa no presentó su estado contable a la CNV después del plazo legal.

Cada incidente se avisa **una sola vez** por email, y solo a `ADMIN_EMAILS` (nunca a
clientes). Se cierra solo cuando la fuente vuelve a andar o el estado aparece; si después
vuelve a fallar, es un incidente nuevo.
"""

import calendar
import hashlib
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from brujula.core.email import EmailSender
from brujula.core.http import FetchError
from brujula.core.integrations import SourceRun
from brujula.features.integrations.models import IntegrationIncident, IntegrationRun

logger = structlog.get_logger(__name__)

MONITOR_FILE = "monitor.yaml"
STATEMENTS_SOURCE = "cnv"
# Cierres trimestrales hacia atrás que se miran para encontrar el último vencido.
LOOKBACK_QUARTERS = 4


class MonitorConfigError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CnvDeadlines(_Strict):
    anual_dias: int = Field(gt=0)
    trimestral_dias: int = Field(gt=0)
    margen_dias: int = Field(ge=0)


class MonitorConfig(_Strict):
    version: Literal[1]
    fallas_consecutivas: int = Field(gt=0)
    plazos_cnv: CnvDeadlines
    retencion_corridas_dias: int = Field(gt=0)


def load_monitor_config(config_dir: Path) -> MonitorConfig:
    path = config_dir / MONITOR_FILE
    try:
        return MonitorConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise MonitorConfigError(f"{path}: {exc}") from exc


# --- Plazos de la CNV ---------------------------------------------------------------------


def quarter_ends(fiscal_year_end: str, before: date) -> list[date]:
    """Cierres trimestrales del ejercicio anteriores a `before`, del más reciente al más viejo."""
    month = int(fiscal_year_end[:2])
    months = {(month - 1 + 3 * k) % 12 + 1 for k in range(4)}
    ends: list[date] = []
    year, current = before.year, before.month
    while len(ends) < LOOKBACK_QUARTERS:
        if current in months:
            end = date(year, current, calendar.monthrange(year, current)[1])
            if end < before:
                ends.append(end)
        current -= 1
        if current == 0:
            year, current = year - 1, 12
    return ends


def expected_statement(fiscal_year_end: str, today: date, deadlines: CnvDeadlines) -> date | None:
    """El último cierre cuyo plazo de presentación (más el margen) ya venció."""
    for end in quarter_ends(fiscal_year_end, today):
        annual = end.strftime("%m-%d") == fiscal_year_end
        days = deadlines.anual_dias if annual else deadlines.trimestral_dias
        if end + timedelta(days=days + deadlines.margen_dias) < today:
            return end
    return None


# --- Monitor ------------------------------------------------------------------------------


class IntegrationMonitor:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], config: MonitorConfig
    ) -> None:
        self._sessions = session_factory
        self._config = config

    async def record(self, runs: Sequence[SourceRun], now: datetime) -> None:
        """Guarda las corridas y abre o cierra los incidentes de falla y de formato."""
        latest = {(run.fuente, run.objeto): run for run in runs}
        async with self._sessions() as session, session.begin():
            session.add_all(
                IntegrationRun(
                    fuente=run.fuente,
                    objeto=run.objeto,
                    ok=run.ok,
                    motivo=run.motivo,
                    formato=run.formato,
                    creado_en=now,
                )
                for run in runs
            )
            await session.flush()
            for run in latest.values():
                if run.ok:
                    await self._close(session, run.fuente, run.objeto, now)
                elif run.formato:
                    detail = f"respuesta con formato inesperado ({run.motivo})"
                    await self._open(session, "formato", run.fuente, run.objeto, detail, now)
                elif await self._failing_streak(session, run):
                    detail = (
                        f"{self._config.fallas_consecutivas} corridas seguidas con error"
                        f" (último motivo: {run.motivo})"
                    )
                    await self._open(session, "falla_repetida", run.fuente, run.objeto, detail, now)
            cutoff = now - timedelta(days=self._config.retencion_corridas_dias)
            await session.execute(delete(IntegrationRun).where(IntegrationRun.creado_en < cutoff))

    async def _failing_streak(self, session: AsyncSession, run: SourceRun) -> bool:
        streak = self._config.fallas_consecutivas
        recent = (
            await session.scalars(
                select(IntegrationRun.ok)
                .where(IntegrationRun.fuente == run.fuente, IntegrationRun.objeto == run.objeto)
                .order_by(IntegrationRun.creado_en.desc())
                .limit(streak)
            )
        ).all()
        return len(recent) == streak and not any(recent)

    @staticmethod
    async def _open(
        session: AsyncSession, kind: str, source: str, subject: str, detail: str, now: datetime
    ) -> None:
        # Si ya hay uno abierto igual, no se abre otro (y no se vuelve a avisar).
        await session.execute(
            insert(IntegrationIncident)
            .values(tipo=kind, fuente=source, objeto=subject, detalle=detail, abierto_en=now)
            .on_conflict_do_nothing(
                index_elements=["tipo", "fuente", "objeto"],
                index_where=IntegrationIncident.cerrado_en.is_(None),
            )
        )

    @staticmethod
    async def _close(session: AsyncSession, source: str, subject: str, now: datetime) -> None:
        await session.execute(
            update(IntegrationIncident)
            .where(
                IntegrationIncident.fuente == source,
                IntegrationIncident.objeto == subject,
                IntegrationIncident.tipo.in_(("falla_repetida", "formato")),
                IntegrationIncident.cerrado_en.is_(None),
            )
            .values(cerrado_en=now)
        )

    async def check_statements(
        self,
        fiscal_year_ends: Mapping[str, str],
        published: Mapping[str, set[date]],
        today: date,
        now: datetime,
    ) -> None:
        """Abre un incidente por cada estado contable vencido y no presentado.

        `fiscal_year_ends`: empresa -> cierre de ejercicio ("MM-DD"). `published`: empresa ->
        cierres con estado contable en la CNV.
        """
        deadlines = self._config.plazos_cnv
        async with self._sessions() as session, session.begin():
            open_missing = (
                await session.scalars(
                    select(IntegrationIncident).where(
                        IntegrationIncident.tipo == "documento_faltante",
                        IntegrationIncident.cerrado_en.is_(None),
                    )
                )
            ).all()
            for incident in open_missing:
                company, _, period = incident.objeto.partition(" ")
                if date.fromisoformat(period) in published.get(company, set()):
                    incident.cerrado_en = now
            for company, fiscal_end in fiscal_year_ends.items():
                expected = expected_statement(fiscal_end, today, deadlines)
                if expected is None or expected in published.get(company, set()):
                    continue
                annual = expected.strftime("%m-%d") == fiscal_end
                days = deadlines.anual_dias if annual else deadlines.trimestral_dias
                kind = "anual" if annual else "trimestral"
                detail = (
                    f"no se presentó a la CNV el estado contable {kind} con cierre {expected}"
                    f" (vencía el {expected + timedelta(days=days)})"
                )
                await self._open(
                    session,
                    "documento_faltante",
                    STATEMENTS_SOURCE,
                    f"{company} {expected}",
                    detail,
                    now,
                )

    async def notify(self, sender: EmailSender, recipients: Sequence[str], now: datetime) -> int:
        """Un email con los incidentes abiertos que todavía no se avisaron. Devuelve cuántos.

        Si el envío falla, quedan sin avisar y se reintenta en la próxima corrida.
        """
        async with self._sessions() as session, session.begin():
            pending = (
                await session.scalars(
                    select(IntegrationIncident)
                    .where(
                        IntegrationIncident.avisado_en.is_(None),
                        IntegrationIncident.cerrado_en.is_(None),
                    )
                    .order_by(IntegrationIncident.abierto_en, IntegrationIncident.fuente)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            if not pending:
                return 0
            ids = ",".join(sorted(str(incident.id) for incident in pending))
            try:
                await sender.send(
                    to=recipients,
                    subject=f"Brújula: {len(pending)} incidente(s) en las integraciones",
                    text=incident_email(pending),
                    idempotency_key=hashlib.sha256(ids.encode()).hexdigest(),
                )
            except FetchError as exc:
                logger.error("monitor_aviso_no_enviado", motivo=exc.reason, incidentes=len(pending))
                return 0
            for incident in pending:
                incident.avisado_en = now
        logger.info("monitor_aviso_enviado", incidentes=len(pending))
        return len(pending)


KIND_TITLES = {
    "falla_repetida": "Falla repetida",
    "formato": "Cambio de formato",
    "documento_faltante": "Documento no publicado",
}


def incident_email(incidents: Sequence[IntegrationIncident]) -> str:
    lines = [
        "Aviso interno del monitor de integraciones de Brújula (solo administradores).",
        "",
    ]
    for incident in incidents:
        subject = f" / {incident.objeto}" if incident.objeto else ""
        lines.append(f"- {KIND_TITLES[incident.tipo]}: {incident.fuente}{subject}")
        lines.append(f"  {incident.detalle}")
        lines.append(f"  desde {incident.abierto_en:%Y-%m-%d %H:%M} UTC")
    lines += [
        "",
        "Cada incidente se avisa una sola vez. Se cierra solo cuando la fuente vuelve a",
        "responder bien o cuando aparece el documento.",
    ]
    return "\n".join(lines)
