"""Cliente LLM con el proveedor simulado: costo, reintentos y tope de presupuesto (T0.5)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from litellm import exceptions as litellm_errors
from pydantic import BaseModel, SecretStr

from brujula.core.config import Settings
from brujula.core.llm.client import (
    BudgetExceededError,
    LLMClient,
    LLMConfigError,
    LLMOutputError,
    LLMPurpose,
    LLMRequestError,
    LLMUnavailableError,
)
from brujula.core.llm.prompts import Prompt
from brujula.core.llm.records import LLMCallRecord

pytestmark = pytest.mark.anyio

MODEL = "anthropic/claude-haiku-4-5"
COST_PER_CALL = 0.0125
PROMPT = Prompt(
    name="clasificar_noticia",
    version="1",
    system="Clasificá la noticia.",
    user_template="Título: ${titulo}",
    params={"max_tokens": 256},
)


class Classification(BaseModel):
    importancia: str
    empresas: list[str]


VALID = '{"importancia": "alta", "empresas": ["YPF"]}'
INVALID = '{"importancia": "alta"}'


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class FakeRecorder:
    spent: Decimal = Decimal(0)
    calls: list[LLMCallRecord] = field(default_factory=list)
    since: list[datetime] = field(default_factory=list)

    async def record(self, call: LLMCallRecord) -> None:
        self.calls.append(call)

    async def cost_since(self, since: datetime) -> Decimal:
        self.since.append(since)
        return self.spent


@dataclass
class FakeGeneration:
    finished: list[dict[str, Any]] = field(default_factory=list)
    trace_id: str | None = "trace-123"

    def finish(self, **kwargs: Any) -> None:
        self.finished.append(kwargs)


@dataclass
class FakeTracer:
    generations: list[FakeGeneration] = field(default_factory=list)

    def start_generation(self, **_: Any) -> FakeGeneration:
        generation = FakeGeneration()
        self.generations.append(generation)
        return generation


def response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30),
    )


@dataclass
class FakeProvider:
    """Devuelve (o lanza) los elementos de `script` en orden y guarda cada pedido."""

    script: list[Any]
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return response(item)


def rate_limit() -> Exception:
    return litellm_errors.RateLimitError(message="límite", llm_provider="anthropic", model=MODEL)


@pytest.fixture
def llm_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "llm_extraction_model": "anthropic/claude-sonnet-5-5",
            "llm_classification_model": MODEL,
            "llm_writer_model": "anthropic/claude-sonnet-5-5",
            "llm_judge_model": "anthropic/claude-opus-5-5",
            "llm_monthly_budget_usd": Decimal("10"),
            "anthropic_api_key": SecretStr("clave-anthropic-de-prueba"),
        }
    )


@dataclass
class Harness:
    recorder: FakeRecorder
    tracer: FakeTracer
    sleeps: list[float]
    build: Callable[[FakeProvider], LLMClient]


@pytest.fixture
def harness(llm_settings: Settings) -> Harness:
    recorder, tracer, sleeps = FakeRecorder(), FakeTracer(), []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def build(provider: FakeProvider) -> LLMClient:
        return LLMClient(
            llm_settings,
            recorder,
            tracer,
            completion=provider,
            cost=lambda **_: COST_PER_CALL,
            sleep=fake_sleep,
            now=lambda: datetime(2026, 10, 6, 2, 0, tzinfo=UTC),
        )

    return Harness(recorder, tracer, sleeps, build)


async def classify(client: LLMClient) -> Any:
    return await client.complete(
        purpose=LLMPurpose.CLASSIFICATION,
        prompt=PROMPT,
        variables={"titulo": "YPF anunció resultados"},
        output_type=Classification,
    )


async def test_registra_costo_tokens_y_traza(harness: Harness) -> None:
    provider = FakeProvider([VALID])

    result = await classify(harness.build(provider))

    assert result.output == Classification(importancia="alta", empresas=["YPF"])
    assert result.cost_usd == Decimal("0.0125")
    assert result.prompt_ref == "clasificar_noticia@1"
    [call] = harness.recorder.calls
    assert call.success is True
    assert (call.tokens_in, call.tokens_out, call.cost_usd) == (120, 30, Decimal("0.0125"))
    assert (call.purpose, call.model, call.trace_id) == ("clasificacion", MODEL, "trace-123")
    [generation] = harness.tracer.generations
    assert generation.finished[0]["cost_usd"] == Decimal("0.0125")


async def test_pide_el_modelo_del_proposito_con_su_clave_y_parametros(harness: Harness) -> None:
    provider = FakeProvider([VALID])

    await classify(harness.build(provider))

    [request] = provider.requests
    assert request["model"] == MODEL
    assert request["api_key"] == "clave-anthropic-de-prueba"
    assert request["response_format"] is Classification
    assert request["max_tokens"] == 256
    assert request["num_retries"] == 0  # los reintentos los controla el cliente
    assert request["messages"][1]["content"] == "Título: YPF anunció resultados"


async def test_reintenta_errores_transitorios_con_backoff(harness: Harness) -> None:
    provider = FakeProvider([rate_limit(), rate_limit(), VALID])

    result = await classify(harness.build(provider))

    assert result.attempts == 3
    assert [c.success for c in harness.recorder.calls] == [False, False, True]
    assert harness.recorder.calls[0].error == "RateLimitError"
    assert len(harness.sleeps) == 2
    assert harness.sleeps[1] > harness.sleeps[0]  # backoff exponencial


async def test_se_rinde_despues_de_tres_errores_transitorios(harness: Harness) -> None:
    provider = FakeProvider([rate_limit(), rate_limit(), rate_limit(), VALID])

    with pytest.raises(LLMUnavailableError):
        await classify(harness.build(provider))

    assert len(harness.recorder.calls) == 3
    assert len(provider.script) == 1  # no hubo cuarto intento


async def test_no_reintenta_errores_definitivos(harness: Harness) -> None:
    auth_error = litellm_errors.AuthenticationError(
        message="clave inválida", llm_provider="anthropic", model=MODEL
    )
    provider = FakeProvider([auth_error, VALID])

    with pytest.raises(LLMRequestError):
        await classify(harness.build(provider))

    assert len(provider.requests) == 1
    assert harness.recorder.calls[0].error == "AuthenticationError"


async def test_salida_invalida_se_reintenta_una_vez_con_el_error(harness: Harness) -> None:
    provider = FakeProvider([INVALID, VALID])

    result = await classify(harness.build(provider))

    assert result.attempts == 2
    assert result.cost_usd == Decimal("0.0250")  # se pagan los dos intentos
    retry_messages = provider.requests[1]["messages"]
    assert retry_messages[-2] == {"role": "assistant", "content": INVALID}
    assert "empresas" in retry_messages[-1]["content"]
    assert [c.error for c in harness.recorder.calls] == ["salida_invalida", None]


async def test_salida_invalida_dos_veces_falla(harness: Harness) -> None:
    provider = FakeProvider([INVALID, INVALID, VALID])

    with pytest.raises(LLMOutputError):
        await classify(harness.build(provider))

    assert len(provider.requests) == 2


async def test_corta_por_presupuesto_sin_llamar_al_modelo(harness: Harness) -> None:
    harness.recorder.spent = Decimal("10.00")
    provider = FakeProvider([VALID])

    with pytest.raises(BudgetExceededError):
        await classify(harness.build(provider))

    assert provider.requests == []
    assert harness.recorder.calls == []


async def test_corta_entre_reintentos_si_se_agota_el_presupuesto(harness: Harness) -> None:
    provider = FakeProvider([INVALID, VALID])
    client = harness.build(provider)
    original = harness.recorder.record

    async def record_and_spend(call: LLMCallRecord) -> None:
        await original(call)
        harness.recorder.spent = Decimal("10.00")

    harness.recorder.record = record_and_spend  # type: ignore[method-assign]

    with pytest.raises(BudgetExceededError):
        await classify(client)

    assert len(provider.requests) == 1


async def test_el_mes_se_cuenta_en_la_zona_horaria_de_la_app(harness: Harness) -> None:
    # 2026-10-06 02:00 UTC es 2026-10-05 23:00 en Buenos Aires: el mes empieza el 1/10 local.
    await classify(harness.build(FakeProvider([VALID])))

    [since] = harness.recorder.since
    assert since.isoformat() == "2026-10-01T00:00:00-03:00"


def test_rechaza_proveedor_sin_clave(llm_settings: Settings) -> None:
    gemini = llm_settings.model_copy(update={"llm_judge_model": "gemini/gemini-2.5-flash"})

    with pytest.raises(LLMConfigError, match="proveedor sin clave"):
        LLMClient(gemini, FakeRecorder(), FakeTracer())


def test_rechaza_modelo_sin_precio_conocido(llm_settings: Settings) -> None:
    unknown = llm_settings.model_copy(update={"llm_judge_model": "anthropic/modelo-inventado"})

    with pytest.raises(LLMConfigError, match="precio"):
        LLMClient(unknown, FakeRecorder(), FakeTracer())


async def test_puede_usar_otro_modelo_para_comparar(harness: Harness) -> None:
    provider = FakeProvider([VALID])

    result = await harness.build(provider).complete(
        purpose=LLMPurpose.CLASSIFICATION,
        prompt=PROMPT,
        variables={"titulo": "YPF"},
        output_type=Classification,
        model="openai/gpt-4o-mini",
    )

    assert provider.requests[0]["model"] == "openai/gpt-4o-mini"
    assert result.model == "openai/gpt-4o-mini"
    assert harness.recorder.calls[0].model == "openai/gpt-4o-mini"


async def test_otro_modelo_sin_clave_se_rechaza(harness: Harness) -> None:
    with pytest.raises(LLMConfigError, match="sin clave"):
        await harness.build(FakeProvider([VALID])).complete(
            purpose=LLMPurpose.CLASSIFICATION,
            prompt=PROMPT,
            variables={"titulo": "YPF"},
            output_type=Classification,
            model="gemini/gemini-2.5-flash",
        )


async def test_envia_imagenes_junto_al_texto(harness: Harness) -> None:
    provider = FakeProvider([VALID])

    await harness.build(provider).complete(
        purpose=LLMPurpose.CLASSIFICATION,
        prompt=PROMPT,
        variables={"titulo": "YPF"},
        output_type=Classification,
        images=[b"\x89PNG-pagina-3"],
    )

    content = provider.requests[0]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "Título: YPF"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,iVBORy1wYWdpbmEtMw")
