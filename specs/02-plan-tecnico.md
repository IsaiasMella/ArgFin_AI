# 02 — Plan técnico (MVP)

## 1. Visión general

Dos repositorios:

- **`brujula-api`** (este plan, salvo la sección 12): monolito modular en Python. Dos procesos con el mismo código: **API** (FastAPI) y **worker** (cola de tareas sobre PostgreSQL).
- **`brujula-web`**: frontend en Next.js (sección 12).

```
[Next.js en Vercel] ──HTTPS──> [Caddy] ──> [API FastAPI] ──┐
                                                           ├──> [PostgreSQL + pgvector]
                               [Worker Procrastinate] ─────┘
                                     │
          ┌──────────────────────────┼──────────────────────────┐
     BYMA Open Data / data912    SEC EDGAR (XBRL)     PDFs CNV / IR, RSS de noticias
                                     │
                         LLMs vía LiteLLM ──> Langfuse (trazas, costos)
                                     │
                               Resend (emails)
```

## 2. Stack y decisiones (ADRs resumidos)

| Componente | Elección | Por qué |
|---|---|---|
| Lenguaje | Python 3.12 | Ecosistema de datos, PDFs, evals y SDKs de LLM. |
| API | FastAPI + Pydantic v2 | Tipado, validación y esquemas reutilizados para structured outputs. |
| ORM y migraciones | SQLAlchemy 2.0 (async, psycopg 3) + Alembic | Maduro, tipado, migraciones versionadas. Un solo driver para ORM, migraciones y cola (ADR 003). |
| Base de datos | PostgreSQL 16 + pgvector | Una sola base para datos, embeddings, cola de tareas y RLS. |
| Cola de tareas | Procrastinate | Cola sobre PostgreSQL: evita sumar Redis; suficiente para cientos de documentos por día; escalar = más workers. Su esquema se aplica con Alembic (ADR 003). |
| Acceso a LLMs | LiteLLM + structured outputs nativos | Interfaz única para comparar modelos y medir costo por llamada. Sin LangChain: el sistema es un pipeline fijo, no un agente. |
| Validación de outputs | Pydantic + reintento con el error de validación | Garantiza esquema; los errores vuelven al modelo una vez antes de fallar. |
| Observabilidad LLM | Langfuse (plan cloud gratuito) | Trazas, costo y latencia. Autoalojarlo exige ClickHouse, Redis y almacenamiento: no vale para el MVP. |
| Logs | structlog (JSON) | Logs estructurados con `request_id` y `job_id`. |
| Extracción de PDFs | PyMuPDF para texto; envío del PDF al modelo multimodal solo si la página es escaneada o tiene tablas complejas | Minimiza costo; la decisión se toma por página y se mide en evals. |
| Datos de EE.UU. | SEC EDGAR API (`companyfacts`, XBRL) | Estructurado y oficial: no requiere LLM para extraer cifras. |
| Precios | BYMA Open Data (PyOBD) principal; data912 respaldo | Gratis; sin API oficial disponible para no miembros. Detrás de una interfaz `PriceProvider`. |
| Embeddings | OpenAI `text-embedding-3-large`, reducido a `LLM_EMBEDDING_DIMENSIONS` (por ejemplo 1536) | Mejor calidad que `small` en benchmarks; el modelo admite acortar el vector sin perder mucha calidad, y los índices HNSW de pgvector para el tipo `vector` admiten hasta 2000 dimensiones. Se confirma en las evals de T4.4. |
| Almacenamiento de documentos | Disco del VPS (`DOCUMENT_STORAGE_DIR`) detrás de una interfaz `DocumentStorage`, incluido en los backups diarios | Lo más simple para el MVP; los PDFs son públicos pero se guardan porque las fuentes pueden desaparecer (trazabilidad, constitución punto 2). Migrable a almacenamiento de objetos compatible con S3 sin tocar las features. |
| Emails | Resend | API simple y webhooks de apertura. |
| Pagos (fase 9) | Mercado Pago (suscripciones / preapproval) | Cliente argentino, cobro en pesos. |
| Autenticación | OAuth 2.0 / OIDC con Google vía Authlib, sesiones del lado del servidor | La API es la única fuente de identidad; facilita RLS. |
| Cifrado de campos | `cryptography` (AES-256-GCM) | Cantidades y precio promedio cifrados en reposo. |
| Herramientas | uv, Ruff, mypy (strict), pytest, pre-commit | Calidad y reproducibilidad. |
| Contenedores | Docker + Docker Compose | API, worker, PostgreSQL y Caddy en un VPS. |
| Proxy | Caddy | TLS automático. |

Cada decisión no trivial nueva se documenta como ADR en `docs/adr/NNN-titulo.md`.

## 3. Estructura del repositorio `brujula-api`

```
brujula-api/
├── AGENTS.md
├── ROADMAP.md
├── specs/
├── docs/adr/
├── pyproject.toml
├── uv.lock
├── .env.example
├── .gitignore
├── docker/
│   ├── Dockerfile
│   ├── compose.yml
│   └── Caddyfile
├── config/                      # reglas de negocio versionadas (sin secretos)
│   ├── universe.yaml
│   ├── factors.yaml
│   ├── signals.yaml
│   ├── news_sources.yaml
│   ├── metrics_by_sector.yaml
│   └── lenguaje_prohibido.yaml
├── prompts/                     # prompts versionados (nombre@versión)
├── migrations/                  # Alembic
├── src/brujula/
│   ├── main.py                  # fábrica de la app FastAPI
│   ├── worker.py                # entrada del worker
│   ├── core/
│   │   ├── config.py            # pydantic-settings: lee .env
│   │   ├── db.py                # engine, sesiones, contexto RLS
│   │   ├── security/            # cifrado de campos, sesiones, CSRF
│   │   ├── llm/                 # cliente LiteLLM, registro de prompts, costos, reintentos
│   │   └── logging.py
│   └── features/
│       ├── auth/
│       ├── portfolios/
│       ├── universe/
│       ├── market_data/
│       ├── filings/
│       ├── extraction/
│       ├── news/
│       ├── exposure/
│       ├── signals/
│       ├── reports/
│       ├── delivery/
│       ├── analytics/
│       └── billing/
├── evals/
│   ├── datasets/
│   ├── runners/
│   └── results/
└── tests/
```

Cada feature contiene, cuando aplique: `models.py` (SQLAlchemy), `schemas.py` (Pydantic), `repository.py`, `service.py`, `router.py`, `tasks.py` (tareas del worker). Las features se comunican **solo a través de sus servicios**, nunca accediendo a modelos o repositorios de otra feature.

## 4. Configuración

- `core/config.py` define un `Settings` con `pydantic-settings`. **Toda** configuración se lee de ahí; ningún módulo lee variables de entorno por su cuenta.
- Todas las variables son **obligatorias**, salvo las marcadas como opcionales (funcionalidades de fases posteriores). El código no define valores por defecto: si falta una obligatoria, la app no arranca y el mensaje lista cuáles faltan (sin mostrar valores).
- `.env.example` (sin valores reales):

```
# App
APP_ENV=
APP_BASE_URL=
WEB_BASE_URL=
COOKIE_DOMAIN=
LOG_LEVEL=
APP_TIMEZONE=                    # p. ej. America/Argentina/Buenos_Aires; usada por los cron

# Base de datos
DATABASE_URL=
DATABASE_URL_MIGRATIONS=

# Seguridad
SESSION_SECRET=
FIELD_ENCRYPTION_KEY=
FIELD_ENCRYPTION_KEYS_PREVIOUS=        # opcional: solo durante una rotación
CSRF_SECRET=
ADMIN_EMAILS=                    # emails separados por coma que reciben el rol admin al ingresar
SESSION_TTL_HOURS=
RATE_LIMIT_AUTH_PER_MINUTE=
RATE_LIMIT_UPLOAD_PER_HOUR=
CSV_MAX_BYTES=
CSV_MAX_ROWS=

# OAuth (Google)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=
GOOGLE_DISCOVERY_URL=

# LLMs (nombres de modelo configurables)
LLM_EXTRACTION_MODEL=
LLM_EXTRACTION_FALLBACK_MODEL=   # opcional
LLM_CLASSIFICATION_MODEL=
LLM_WRITER_MODEL=
LLM_EMBEDDING_MODEL=
LLM_EMBEDDING_DIMENSIONS=        # <= 2000 (límite de índices HNSW de pgvector)
LLM_JUDGE_MODEL=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
LLM_MONTHLY_BUDGET_USD=

# Observabilidad
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=

# Fuentes de datos
SEC_USER_AGENT=
BYMA_OPEN_DATA_BASE_URL=
DATA912_BASE_URL=
PRICE_DIVERGENCE_THRESHOLD_PCT=

# Programación de tareas (cron)
CRON_PRICES_DAILY=
CRON_FILINGS_CHECK=
CRON_NEWS_INGEST=
CRON_WEEKLY_DIGEST=

# Email
RESEND_API_KEY=
EMAIL_FROM=
RESEND_WEBHOOK_SECRET=

# Negocio
PRICE_PRO_ARS=
FREE_PLAN_MAX_POSITIONS=
FREE_PLAN_DIGESTS_PER_MONTH=
FOUNDER_PLAN_OPEN=               # true durante el MVP: todo registro nuevo es plan fundador
VALIDATION_THRESHOLD_PCT=        # opcional hasta T9.1

# Pagos (fase 9, opcionales hasta entonces)
MERCADOPAGO_ACCESS_TOKEN=
MERCADOPAGO_WEBHOOK_SECRET=

# Almacenamiento
DOCUMENT_STORAGE_DIR=

# Rutas de configuración
CONFIG_DIR=
PROMPTS_DIR=
```

## 5. Modelo de datos (resumen)

**Tablas compartidas** (sin datos de usuario, sin RLS de usuario):

- `companies`: id, clave (estable, la del YAML), nombre, sector, país, tipo (`ar_equity`, `cedear`), cik_sec, url_relacion_inversores, activa.
- `instruments`: id, company_id, ticker_byma, ticker_origen, ratio_cedear, moneda, activo. Se cargan desde `config/universe.yaml` con `sincronizar-universo` (ADR 010).
- `prices_daily`: instrument_id, fecha, cierre, volumen, fuente, divergencia_pct, marcado (bool).
- `fx_daily`: fecha, ccl, mep, oficial, fuente.
- `documents`: id, company_id, tipo (`estado_contable`, `comunicado_resultados`, `hecho_relevante`, `memoria_anual`), período, url_origen, hash_sha256, ruta_almacenada, fecha_publicacion, estado.
- `financial_facts`: id, document_id, company_id, métrica, período, valor, moneda, unidad, base_medicion (`nominal` | `homogenea`), fecha_reexpresion, es_comparativo, fuente_pagina o fuente_xbrl, confianza, extractor_version.
- `news_items`: id, url, titulo, fuente, fecha, resumen_propio, embedding (vector), cluster_id, importancia.
- `news_company_links`: news_id, company_id, relevancia.
- `news_factor_links`: news_id, factor_id, relevancia.
- `factors`: id, código, nombre, descripción (desde `config/factors.yaml`).
- `exposure_entries`: company_id, factor_id, dirección (`+`, `-`, `ambigua`), intensidad (`alta`, `media`, `baja`), justificación, cita_documento_id, cita_pagina, estado (`propuesta`, `aprobada`, `rechazada`), aprobada_por, aprobada_en.
- `signals_fired`: company_id, período, regla_código, valores_usados (json), fecha.
- `company_reports`: id, company_id, período, contenido_renderizado, versión_prompt, costo_usd, estado.
- `roles`: código (`usuario`, `admin`, …), descripción. Catálogo extensible sin migrar enums; el rol define **permisos** y es independiente del plan. Los emails de `ADMIN_EMAILS` reciben `admin` al ingresar.
- `news_facts`: id, news_id, descripción, valor, unidad, fecha_referencia, url_fuente, extractor_version. Cifras de noticias extraídas como datos con fuente, para usarlas con marcadores (constitución, punto 2).
- `visitor_intents`: id, visitor_id (aleatorio, en una cookie propia), plan, fecha, origen_campaña. Sin datos personales ni `user_id`. El rol de la app solo puede insertar; las métricas se leen con funciones de agregación.
- `llm_calls`: id, propósito, modelo, versión_prompt, tokens_in, tokens_out, costo_usd, latencia_ms, éxito, trace_id.

**Tablas de usuario** (con RLS):

- `users`: id, email, nombre, proveedor_oauth, sub_oauth, rol (FK a `roles`), plan (`gratis`, `pro`, `fundador`), creado_en, borrado_en.
- `sessions`: id, user_id, token_hash, expira_en, ip_hash, user_agent.
- `oauth_transactions` (sin datos de usuario, sin RLS): state_hash, code_verifier, nonce, expira_en. De un solo uso.
- `holdings`: id, user_id, instrument_id o ticker_libre, cantidad_cifrada, precio_promedio_cifrado (opcional), moneda_precio (`ARS` | `USD`, junto con el precio), broker (opcional), creado_en, actualizado_en.
- `user_report_deliveries`: id, user_id, tipo (`semanal`, `trimestral`), referencia, enviado_en, abierto_en.
- `email_preferences`: user_id, semanal_activo, trimestral_activo, token_baja.
- `payment_intents`: id, user_id, plan, fecha, origen_campaña. Si el usuario se registra con una cookie de visitante que tiene intenciones en `visitor_intents`, se copian acá (plan, fecha, campaña); la fila anónima no guarda referencia al usuario.
- `subscriptions` (fase 9): user_id, proveedor, estado, id_externo.

## 6. Seguridad

1. **Secretos:** solo en `.env`. Nunca leer `.env` directamente desde el código ni desde el agente; usar `Settings`. `.env` en `.gitignore`; `.env.example` versionado y sin valores.
2. **RLS:**
   - `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` y `FORCE ROW LEVEL SECURITY` en todas las tablas de usuario.
   - Política: `user_id = current_setting('app.current_user_id')::uuid`.
   - La aplicación se conecta con un rol **sin** privilegios de dueño de tabla ni `BYPASSRLS`. Las migraciones usan otro rol (`DATABASE_URL_MIGRATIONS`).
   - Cada request autenticado ejecuta `SET LOCAL app.current_user_id` dentro de su transacción. El worker hace lo mismo por cada usuario que procesa; no existe un rol que saltee RLS.
   - Las operaciones que necesitan resolver un usuario **antes** de conocer su id (ingreso OAuth, resolución de sesión por `token_hash`) o enumerar usuarios (worker: usuarios activos, usuarios con una empresa) usan funciones `SECURITY DEFINER` acotadas, con `search_path` fijo, que devuelven solo lo mínimo (ids). Se documentan en un ADR en T0.4.
   - Tests que verifican que un usuario no puede leer ni modificar filas de otro.
3. **Autenticación:** OAuth 2.0 / OIDC con Google, Authorization Code + PKCE, validación de `state`, `nonce` y firma del ID token. Sesiones del lado del servidor; en la base solo se guarda el hash del token. Cookie `HttpOnly`, `Secure`, `SameSite=Lax`, dominio compartido entre `app.` y `api.`.
4. **CSRF:** verificación de `Origin` más token doble para métodos que modifican estado.
5. **Cifrado:** TLS en todo el tráfico (Caddy). AES-256-GCM a nivel de campo para cantidades y precio promedio; clave en `.env`; procedimiento de rotación documentado en un ADR.
6. **Superficie mínima:** CORS restringido a `WEB_BASE_URL`; rate limiting en endpoints de autenticación y carga de CSV; validación estricta de CSV (tamaño, columnas, tipos).
7. **Webhooks** (Resend, Mercado Pago): verificación de firma obligatoria.
8. **Datos personales:** borrado de cuenta real (no solo lógico) a pedido del usuario.

## 7. Pipelines

Todas las tareas son **idempotentes**: reintentarlas no duplica datos (claves naturales y `hash_sha256` de documentos).

### 7.1 Precios (diario)
1. Para cada instrumento del universo, pedir el cierre a `BYMA Open Data` y a `data912`.
2. Guardar el principal; calcular divergencia contra el respaldo.
3. Si la divergencia supera `PRICE_DIVERGENCE_THRESHOLD_PCT`, marcar el dato; un dato marcado no se publica en informes sin revisión.
4. Si la fuente principal falla, usar el respaldo y registrarlo.
5. Guardar tipo de cambio CCL diario.

### 7.2 Estados contables
1. **Descubrimiento:** revisar periódicamente las fuentes de cada empresa (relación con inversores, CNV) en busca de documentos nuevos. Las URLs y métodos concretos por empresa se definen en la tarea de discovery (ver `03-tareas.md`, T3.1) y se guardan en `config/universe.yaml`.
2. **Descarga y deduplicación** por hash. Los archivos se guardan con `DocumentStorage` (implementación en disco bajo `DOCUMENT_STORAGE_DIR`).
3. **Empresas con datos en la SEC:** leer `companyfacts` (XBRL) y mapear conceptos a métricas internas sin LLM.
4. **Empresas argentinas (PDF):**
   - Extraer texto por página con PyMuPDF; detectar páginas con tablas complejas o escaneadas y enviarlas al modelo multimodal.
   - Extracción con structured outputs al esquema `FinancialStatementExtraction` (Pydantic): cada métrica con valor, moneda, unidad, período, base de medición, si es comparativo y página fuente.
   - Validaciones determinísticas: identidades contables (activo = pasivo + patrimonio, con tolerancia configurable), signos esperados, unidades coherentes, presencia de las métricas obligatorias del sector (`config/metrics_by_sector.yaml`).
   - Si la validación falla: un reintento con el error; si vuelve a fallar, el documento queda en estado `revision_manual` y no genera informe.
5. Al validar un documento nuevo, encolar la generación del informe trimestral.

### 7.3 Noticias
1. Ingesta de fuentes configuradas en `config/news_sources.yaml` (RSS y hechos relevantes oficiales). Se guarda **solo** título, link, fecha y un resumen propio; nunca el texto completo.
2. Embedding del título y resumen; agrupar duplicados por similitud (umbral configurable).
3. Clasificación con LLM (structured output): empresas mencionadas, relevancia, importancia (`alta`, `media`, `baja`) y factores macro afectados (de la taxonomía de `factors.yaml`).
4. Cifras de la noticia: se extraen como `news_facts` (valor, unidad, fuente). Validación determinística: el valor debe aparecer literalmente en el texto fuente descargado; si no, se descarta.
5. Ruteo: una noticia macro llega a un usuario si afecta un factor al que alguna de sus empresas tiene exposición **aprobada**.

### 7.4 Mapa de exposición
1. Para cada empresa, a partir de la memoria anual o de la sección de factores de riesgo, el LLM propone entradas de exposición con justificación y cita (documento y página).
2. Las propuestas quedan en estado `propuesta` hasta que un administrador las aprueba (endpoint protegido por rol `admin` o comando de CLI).
3. Solo se usan las entradas aprobadas.

### 7.5 Señales
- Motor de reglas determinístico definido en `config/signals.yaml`. Ejemplo de regla: código, descripción en lenguaje neutro, métrica, condición, ventana, umbral, **polaridad** (`fuerte` o `debil`) y **materialidad** (prioridad para ordenar).
- Cada señal disparada guarda los valores que la activaron, para mostrarlos con su fuente.

### 7.6 Informes
1. **Ensamblado de datos:** el código arma un objeto con métricas, variaciones, señales, exposición y noticias.
2. **Puntos fuertes y puntos débiles:** el código selecciona las señales de cada polaridad y las ordena por materialidad. Ambas secciones están siempre presentes, con el mismo tope de ítems configurable; si una no tiene señales, lo dice explícitamente. El LLM no elige qué resaltar (constitución, punto 1).
3. **Redacción:** el LLM escribe el texto narrativo usando **solo** marcadores (`{{metric:ebitda_ajustado:2T26}}`, `{{var:ebitda_ajustado:qoq}}`, `{{source:doc_123:p4}}`).
4. **Validación del texto:**
   - Sin dígitos fuera de marcadores, salvo los patrones de una lista blanca versionada en `config/numeros_permitidos.yaml` (nombres propios como "3M", "G20", "COVID-19"; períodos como "2T26"; normas como "Ley 25.326").
   - Todos los marcadores existen en el objeto de datos.
   - Sin términos de `lenguaje_prohibido.yaml`. Los patrones apuntan a formas de recomendación (infinitivo, imperativo, "conviene", "habría que", segunda persona) y no a hechos en tercera persona ("la empresa vendió su participación").
   - Si falla, un reintento con el error; si vuelve a fallar, el informe queda en `revision_manual`.
5. **Renderizado:** el código reemplaza marcadores y produce HTML para email y vista web.
6. El informe trimestral se genera **una vez por empresa y período**; el envío a cada usuario agrega solo el contexto de su posición (peso en su portafolio).
7. El resumen semanal se arma por usuario, reutilizando bloques ya generados por empresa y por noticia.

## 8. Uso de LLMs

- Todas las llamadas pasan por `core/llm/client.py`: elige el modelo desde `Settings` según el propósito, registra la llamada en `llm_calls` y en Langfuse, aplica reintentos con backoff y corta si se supera `LLM_MONTHLY_BUDGET_USD`.
- Prompts en `prompts/` con nombre y versión; cada resultado guarda la versión del prompt que lo produjo.
- Temperatura baja para extracción y clasificación.
- Nunca se envían datos de usuarios a los LLMs: solo documentos públicos y noticias.

## 9. Evals

Carpeta `evals/`, ejecutables con un comando y con resultados guardados por fecha, modelo y versión de prompt.

| Componente | Dataset de referencia | Métrica | Umbral para avanzar |
|---|---|---|---|
| Extracción de estados contables | 10 empresas × 4 trimestres, cargado y verificado a mano | Exactitud por campo (tolerancia ±0,5 %), moneda/unidad/base correctas | ≥ 95 % de campos correctos y 100 % de moneda/unidad correctas |
| Clasificación de noticias | 200 noticias etiquetadas a mano | Precisión y recall de relevancia por empresa; exactitud de importancia | Precisión ≥ 90 %, recall ≥ 80 % |
| Ruteo por factores | 100 noticias macro etiquetadas | Exactitud de factores asignados | ≥ 85 % |
| Mapa de exposición | Revisión humana de propuestas | Tasa de aprobación sin edición | Seguimiento (sin umbral bloqueante) |
| Redacción de informes | 30 informes | Validaciones determinísticas + juez LLM de fidelidad a las fuentes | 100 % de validaciones; fidelidad ≥ 95 % |

Los umbrales son configurables y se documentan en un ADR. Cada eval reporta también **costo y latencia promedio**, y permite comparar modelos (por ejemplo, extracción con dos modelos distintos), con resultados exportados a una tabla para el README.

**Elección de modelo por costo:** cada eval de un componente con LLM compara al menos un modelo de bajo costo de **otro proveedor** contra el modelo de Claude configurado. Se usa el modelo **más barato que cumpla el umbral** de la tabla; el precio de lista no decide por sí solo. Los precios se verifican al momento de correr la eval. Agregar un proveedor nuevo implica sumar su clave en `Settings` y en `.env.example` (el cliente LLM rechaza modelos sin clave).

## 10. Observabilidad

- Langfuse: una traza por documento procesado y por informe generado.
- Logs JSON con `request_id`, `job_id`, `user_id` (hasheado).
- Endpoints `/health` (vivo) y `/ready` (base de datos y cola disponibles).
- Panel mínimo (consulta SQL o endpoint de admin): costo por documento, costo por informe, documentos en `revision_manual`, precios marcados.

## 11. Despliegue

- VPS con Docker Compose: contenedores `api`, `worker`, `postgres` y `caddy`.
- Una sola imagen para API y worker, con distinto comando de entrada.
- Migraciones con Alembic al desplegar, usando el rol de migraciones.
- Backups diarios de PostgreSQL (`pg_dump`) y de `DOCUMENT_STORAGE_DIR` a almacenamiento externo, con retención configurable y prueba de restauración documentada.
- CI (GitHub Actions): Ruff, mypy, pytest y build de la imagen en cada push; evals en ejecución manual o nocturna.

## 12. Frontend (`brujula-web`, repositorio aparte)

- Next.js (App Router), TypeScript estricto, Tailwind CSS.
- Páginas: landing, informe de ejemplo, precios, ingresar (redirige al flujo OAuth de la API), panel de portafolio (alta, edición, baja, carga de CSV), vista de informes, preferencias de email, términos, privacidad y disclaimer.
- Todas las llamadas a la API con `credentials: 'include'`; sin tokens en `localStorage`.
- Variables de entorno en `.env` con `.env.example`; solo la URL pública de la API se expone al cliente.
- Despliegue en Vercel.

## 13. Riesgos técnicos conocidos

| Riesgo | Mitigación |
|---|---|
| Fuentes de precios no oficiales cambian sin aviso | Interfaz `PriceProvider`, respaldo, alertas por falla |
| PDFs con formatos muy distintos | Validaciones contables, estado `revision_manual`, evals por empresa |
| Costo de LLM fuera de control | Presupuesto mensual con corte, procesamiento por documento |
| Términos de uso de datos para fines comerciales | Revisión legal antes de cobrar |
| Alucinación de cifras | Números insertados por código (constitución, punto 2) |
