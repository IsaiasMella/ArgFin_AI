# 005 — Cliente LLM: costo, reintentos, tope mensual y trazas

- **Estado:** aceptada
- **Fecha:** 2026-10-06

## Contexto

La constitución exige medir costo, tokens y latencia de cada llamada (punto 5), y el plan
pide un tope mensual con corte, reintentos y prompts versionados (sección 8). El sistema es
un pipeline fijo, no un agente: no se necesita un framework de orquestación.

## Decisión

1. **Un solo punto de entrada,** `core/llm/client.py`, sobre **LiteLLM**. El modelo se elige
   por propósito (`extraccion`, `clasificacion`, `redaccion`, `juez`) desde `Settings`, así
   que cambiar de proveedor es cambiar el `.env`. La clave se pasa por llamada según el
   prefijo del modelo (`anthropic/`, `openai/`).
2. **Al arrancar** se rechaza un modelo sin clave configurada o sin precio conocido en
   LiteLLM: sin precio no se puede controlar el presupuesto.
3. **Salida estructurada** con `response_format=<modelo Pydantic>` (LiteLLM usa el modo
   nativo del proveedor) y **validación con Pydantic** de todos modos. Si no valida, se
   reenvía una vez con los errores (sin los valores de entrada); si vuelve a fallar,
   `LLMOutputError`.
4. **Errores transitorios** (rate limit, timeouts, 5xx, conexión): hasta 3 intentos con
   backoff exponencial y jitter. Los errores definitivos (autenticación, pedido inválido)
   no se reintentan. LiteLLM corre con `num_retries=0` para que la política sea una sola.
5. **Tope mensual:** antes de **cada** intento se suma `llm_calls.costo_usd` desde el
   inicio del mes en `APP_TIMEZONE`; si alcanzó `LLM_MONTHLY_BUDGET_USD`, se corta sin
   llamar y se registra un error en los logs. Con varios workers en paralelo el corte es
   aproximado (pueden pasar las llamadas en vuelo); a esta escala es aceptable.
6. **Se registra cada intento** en `llm_calls`, también los fallidos y los inválidos (se
   pagan igual), con la versión del prompt y el `trace_id` de Langfuse.
7. **Langfuse** recibe una *generation* por intento. Si falla, solo se registra una
   advertencia: la observabilidad nunca rompe el pipeline.
8. **Prompts** en `PROMPTS_DIR/<nombre>@<versión>.yaml`, inmutables una vez publicados,
   con variables `${nombre}` (`string.Template`: las llaves de un JSON de ejemplo no se
   confunden con variables).
9. **Parámetros de muestreo por prompt** (`parametros` en el YAML), no globales: los modelos
   Claude 5.x rechazan `temperature`/`top_p`, así que la "temperatura baja" del plan aplica
   solo a los modelos que la aceptan.
10. **Logs** JSON con structlog para todo (incluidos Uvicorn y Procrastinate), con
    `request_id` por request (se acepta el del proxy solo si tiene un formato seguro).

## Alternativas consideradas

- SDKs de cada proveedor: más control fino, pero hay que reimplementar costo, reintentos y
  salida estructurada por proveedor, y comparar modelos en las evals se vuelve caro.
- El callback de Langfuse que trae LiteLLM: lee las claves de variables de entorno globales,
  fuera de `Settings`.

## Consecuencias

- Los precios salen del mapa de LiteLLM; al actualizar la librería pueden cambiar.
- Los procesos que usen el cliente deben llamar a `flush()` de Langfuse al terminar (se
  conecta cuando el worker ejecute las primeras tareas con LLM, en la fase 3).
- La salida estructurada nativa con Claude 5.x se verifica con claves reales en T3.5.
