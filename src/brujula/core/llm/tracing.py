"""Trazas de llamadas a LLMs en Langfuse.

La observabilidad nunca rompe el pipeline: si Langfuse falla, se registra una advertencia
y la llamada sigue.
"""

from decimal import Decimal
from typing import Any, Protocol

import structlog
from langfuse import Langfuse

from brujula.core.config import Settings

logger = structlog.get_logger(__name__)


class Generation(Protocol):
    @property
    def trace_id(self) -> str | None: ...

    def finish(
        self,
        *,
        output: Any,
        tokens_in: int | None,
        tokens_out: int | None,
        cost_usd: Decimal | None,
        error: str | None,
    ) -> None: ...


class LLMTracer(Protocol):
    def start_generation(
        self, *, name: str, model: str, messages: list[dict[str, Any]], metadata: dict[str, Any]
    ) -> Generation: ...


class _NoGeneration:
    trace_id: str | None = None

    def finish(self, **_: Any) -> None:
        return None


class NullTracer:
    """Sin trazas (tests o entornos sin Langfuse)."""

    def start_generation(self, **_: Any) -> Generation:
        return _NoGeneration()


class _LangfuseGeneration:
    def __init__(self, observation: Any) -> None:
        self._observation = observation

    @property
    def trace_id(self) -> str | None:
        trace_id = getattr(self._observation, "trace_id", None)
        return str(trace_id) if trace_id else None

    def finish(
        self,
        *,
        output: Any,
        tokens_in: int | None,
        tokens_out: int | None,
        cost_usd: Decimal | None,
        error: str | None,
    ) -> None:
        try:
            usage = {
                key: value
                for key, value in (("input", tokens_in), ("output", tokens_out))
                if value is not None
            }
            self._observation.update(
                output=output,
                usage_details=usage or None,
                cost_details={"total": float(cost_usd)} if cost_usd is not None else None,
                level="ERROR" if error else None,
                status_message=error,
            )
            self._observation.end()
        except Exception:
            logger.warning("langfuse_error_al_cerrar_traza", exc_info=True)


class LangfuseTracer:
    def __init__(self, client: Langfuse) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "LangfuseTracer":
        return cls(
            Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key.get_secret_value(),
                host=str(settings.langfuse_host),
                environment=settings.app_env,
            )
        )

    def start_generation(
        self, *, name: str, model: str, messages: list[dict[str, Any]], metadata: dict[str, Any]
    ) -> Generation:
        try:
            observation = self._client.start_observation(
                name=name, as_type="generation", model=model, input=messages, metadata=metadata
            )
        except Exception:
            logger.warning("langfuse_error_al_abrir_traza", exc_info=True)
            return _NoGeneration()
        return _LangfuseGeneration(observation)
