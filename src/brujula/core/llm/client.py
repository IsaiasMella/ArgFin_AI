"""Cliente único para LLMs (vía LiteLLM). Ver docs/adr/005.

Por cada llamada:
1. Corta antes de llamar si el gasto del mes ya alcanzó `LLM_MONTHLY_BUDGET_USD`.
2. Elige el modelo según el propósito (desde `Settings`) y pide salida estructurada.
3. Reintenta con backoff los errores transitorios del proveedor.
4. Valida la salida con Pydantic; si no valida, reintenta una vez con el error.
5. Registra cada intento en `llm_calls` y en Langfuse (tokens, costo, latencia).
"""

import asyncio
import json
import random
import time
from base64 import b64encode
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

import litellm
import openai
import structlog
from litellm import exceptions as litellm_errors
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from brujula.core.config import Settings
from brujula.core.llm.prompts import Prompt
from brujula.core.llm.records import CallRecorder, LLMCallRecord, SqlCallRecorder
from brujula.core.llm.tracing import Generation, LangfuseTracer, LLMTracer

logger = structlog.get_logger(__name__)

# Política de reintentos ante fallas transitorias del proveedor (no ante respuestas inválidas).
MAX_TRANSIENT_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0
# Una respuesta que no valida vuelve al modelo con el error una sola vez (plan técnico, §2).
MAX_VALIDATION_RETRIES = 1

TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    litellm_errors.RateLimitError,
    litellm_errors.APIConnectionError,
    litellm_errors.Timeout,
    litellm_errors.ServiceUnavailableError,
    litellm_errors.InternalServerError,
    litellm_errors.BadGatewayError,
)

VALIDATION_FEEDBACK = (
    "Tu respuesta anterior no cumple el esquema pedido. Errores de validación:\n"
    "{errors}\n"
    "Respondé de nuevo solo con un JSON que cumpla el esquema."
)


class LLMPurpose(StrEnum):
    EXTRACTION = "extraccion"
    CLASSIFICATION = "clasificacion"
    WRITER = "redaccion"
    JUDGE = "juez"


class LLMError(RuntimeError):
    """Error base del cliente LLM."""


class BudgetExceededError(LLMError):
    """Se alcanzó el presupuesto mensual: no se hacen más llamadas este mes."""


class LLMUnavailableError(LLMError):
    """El proveedor siguió fallando después de los reintentos."""


class LLMRequestError(LLMError):
    """El proveedor rechazó la llamada (no se reintenta: reintentar no lo arregla)."""


class LLMOutputError(LLMError):
    """La salida no cumplió el esquema ni después del reintento con el error."""


class LLMConfigError(LLMError):
    """Modelo sin precio conocido o proveedor sin clave configurada."""


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    output: T
    model: str
    prompt_ref: str
    cost_usd: Decimal
    attempts: int
    trace_id: str | None


CompletionFn = Callable[..., Awaitable[Any]]
CostFn = Callable[..., float]
SleepFn = Callable[[float], Awaitable[None]]


def _provider(model: str) -> str:
    return model.split("/", 1)[0] if "/" in model else ""


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        recorder: CallRecorder,
        tracer: LLMTracer,
        *,
        completion: CompletionFn = litellm.acompletion,
        cost: CostFn = litellm.completion_cost,
        sleep: SleepFn = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._recorder = recorder
        self._tracer = tracer
        self._completion = completion
        self._cost = cost
        self._sleep = sleep
        self._now = now
        self._budget = settings.llm_monthly_budget_usd
        self._timezone = ZoneInfo(settings.app_timezone)
        self._models = {
            LLMPurpose.EXTRACTION: settings.llm_extraction_model,
            LLMPurpose.CLASSIFICATION: settings.llm_classification_model,
            LLMPurpose.WRITER: settings.llm_writer_model,
            LLMPurpose.JUDGE: settings.llm_judge_model,
        }
        configured = {
            "anthropic": settings.anthropic_api_key,
            "openai": settings.openai_api_key,
            "gemini": settings.gemini_api_key,
        }
        self._api_keys = {name: key for name, key in configured.items() if key is not None}
        for model in self._models.values():
            self._check_model(model)

    def _check_model(self, model: str) -> None:
        # Sin clave o sin precio conocido no se puede llamar ni controlar el presupuesto.
        if _provider(model) not in self._api_keys:
            raise LLMConfigError(
                f"{model}: proveedor sin clave configurada"
                f" (con clave: {', '.join(self._api_keys) or 'ninguno'})"
            )
        try:
            litellm.get_model_info(model)
        except Exception:
            raise LLMConfigError(f"{model}: LiteLLM no conoce su precio") from None

    async def complete[T: BaseModel](
        self,
        *,
        purpose: LLMPurpose,
        prompt: Prompt,
        variables: Mapping[str, str],
        output_type: type[T],
        model: str | None = None,
        images: Sequence[bytes] = (),
    ) -> LLMResult[T]:
        """`model` reemplaza al del propósito (p. ej. para comparar modelos en las evals).
        `images` son PNG que se envían junto al texto (páginas escaneadas)."""
        if model is None:
            model = self._models[purpose]
        else:
            self._check_model(model)
        text = prompt.render_user(variables)
        user_content: str | list[dict[str, Any]] = text
        if images:
            user_content = [
                {"type": "text", "text": text},
                *(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64encode(img).decode()}"},
                    }
                    for img in images
                ),
            ]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": user_content},
        ]
        log = logger.bind(proposito=purpose.value, modelo=model, prompt=prompt.ref)
        total_cost = Decimal(0)
        transient_failures = 0
        validation_retries = 0
        attempt = 0

        while True:
            attempt += 1
            await self._ensure_budget(log)
            generation = self._tracer.start_generation(
                name=prompt.ref, model=model, messages=messages, metadata={"proposito": purpose}
            )
            started = time.perf_counter()
            try:
                response = await self._completion(
                    model=model,
                    messages=messages,
                    response_format=output_type,
                    api_key=self._api_keys[_provider(model)].get_secret_value(),
                    num_retries=0,
                    **prompt.params,
                )
            except TRANSIENT_ERRORS as exc:
                transient_failures += 1
                await self._record_failure(
                    purpose, model, prompt, attempt, started, generation, type(exc).__name__
                )
                if transient_failures >= MAX_TRANSIENT_ATTEMPTS:
                    log.error("llm_no_disponible", intentos=attempt)
                    raise LLMUnavailableError(f"{model}: {type(exc).__name__}") from exc
                delay = BACKOFF_BASE_SECONDS * 2 ** (transient_failures - 1)
                log.warning("llm_error_transitorio", error=type(exc).__name__, espera_s=delay)
                await self._sleep(delay + random.uniform(0, BACKOFF_BASE_SECONDS))  # noqa: S311
                continue
            except openai.APIError as exc:  # base de todas las excepciones de LiteLLM
                await self._record_failure(
                    purpose, model, prompt, attempt, started, generation, type(exc).__name__
                )
                raise LLMRequestError(f"{model}: {type(exc).__name__}") from exc

            latency_ms = int((time.perf_counter() - started) * 1000)
            cost = Decimal(str(self._cost(completion_response=response)))
            total_cost += cost
            usage = getattr(response, "usage", None)
            tokens_in = getattr(usage, "prompt_tokens", None)
            tokens_out = getattr(usage, "completion_tokens", None)
            content = response.choices[0].message.content or ""

            error: str | None = None
            details = ""
            output: T | None = None
            try:
                output = output_type.model_validate_json(content)
            except ValidationError as exc:
                error = "salida_invalida"
                details = json.dumps(
                    exc.errors(include_input=False, include_url=False), ensure_ascii=False
                )

            generation.finish(
                output=content,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                error=error,
            )
            await self._recorder.record(
                LLMCallRecord(
                    purpose=purpose.value,
                    model=model,
                    prompt_ref=prompt.ref,
                    attempt=attempt,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                    success=output is not None,
                    error=error,
                    trace_id=generation.trace_id,
                )
            )

            if output is not None:
                log.info("llm_ok", intentos=attempt, costo_usd=str(total_cost))
                return LLMResult(
                    output=output,
                    model=model,
                    prompt_ref=prompt.ref,
                    cost_usd=total_cost,
                    attempts=attempt,
                    trace_id=generation.trace_id,
                )
            if validation_retries >= MAX_VALIDATION_RETRIES:
                log.error("llm_salida_invalida", intentos=attempt)
                raise LLMOutputError(f"{prompt.ref}: la salida no cumple {output_type.__name__}")
            validation_retries += 1
            log.warning("llm_reintento_por_validacion")
            messages = [
                *messages,
                {"role": "assistant", "content": content},
                {"role": "user", "content": VALIDATION_FEEDBACK.format(errors=details)},
            ]

    async def _ensure_budget(self, log: Any) -> None:
        local_now = self._now().astimezone(self._timezone)
        month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = await self._recorder.cost_since(month_start)
        if spent >= self._budget:
            log.error("llm_presupuesto_agotado", gastado_usd=str(spent), tope_usd=str(self._budget))
            raise BudgetExceededError(
                f"gasto del mes USD {spent} alcanzó el tope USD {self._budget}"
            )

    async def _record_failure(
        self,
        purpose: LLMPurpose,
        model: str,
        prompt: Prompt,
        attempt: int,
        started: float,
        generation: Generation,
        error: str,
    ) -> None:
        generation.finish(output=None, tokens_in=None, tokens_out=None, cost_usd=None, error=error)
        await self._recorder.record(
            LLMCallRecord(
                purpose=purpose.value,
                model=model,
                prompt_ref=prompt.ref,
                attempt=attempt,
                tokens_in=None,
                tokens_out=None,
                cost_usd=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                success=False,
                error=error,
                trace_id=generation.trace_id,
            )
        )


def create_llm_client(settings: Settings, engine: AsyncEngine) -> LLMClient:
    """Cliente real: registra en `llm_calls` (rol de la app) y traza en Langfuse."""
    return LLMClient(settings, SqlCallRecorder(engine), LangfuseTracer.from_settings(settings))
