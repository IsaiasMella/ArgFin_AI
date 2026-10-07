# 03 — Tareas del MVP

Reglas: una tarea por vez, en orden. Cada tarea está terminada cuando cumple sus criterios y pasan `ruff`, `mypy --strict` y `pytest`. Las fases con **gate** no se cruzan sin cumplir su condición.

## Fase 0 — Fundaciones

**T0.1 Repositorio y herramientas.** uv, Ruff, mypy, pytest, pre-commit, `.gitignore` (con `.env`), `.env.example`, `AGENTS.md`, `ROADMAP.md`, carpeta `docs/adr/`.
- Criterio: `uv run pytest` corre; pre-commit bloquea commits con `.env`.

**T0.2 Configuración.** `core/config.py` con `pydantic-settings` y todas las variables de `02-plan-tecnico.md`, sección 4.
- Criterio: la app falla al arrancar con un mensaje claro si falta una variable obligatoria; test que verifica que ningún módulo usa `os.environ` fuera de `config.py`.

**T0.3 Docker.** Dockerfile multi-stage, `compose.yml` con `api`, `worker`, `postgres` (con pgvector) y `caddy`.
- Criterio: `docker compose up` levanta todo y `/health` responde.

**T0.4 Base de datos, roles y RLS.** Alembic, rol de aplicación sin `BYPASSRLS` y rol de migraciones, helper para `SET LOCAL app.current_user_id`. Cola de tareas Procrastinate (esquema por migración) usada por el worker, y endpoint `/ready`.
- Criterio: test que demuestra que, sin contexto de usuario, una tabla de usuario devuelve cero filas.

**T0.5 Logging y cliente LLM.** structlog; `core/llm/client.py` con LiteLLM, registro en `llm_calls` y Langfuse, reintentos y corte por presupuesto; registro de prompts versionados.
- Criterio: tests con el LLM simulado verifican registro de costo, reintento y corte por presupuesto.

**T0.6 CI.** GitHub Actions con lint, tipos, tests y build.

## Fase 1 — Autenticación y portafolios

**T1.1 OAuth 2.0 / OIDC con Google.** PKCE, `state`, `nonce`, validación del ID token, sesiones del lado del servidor, logout, CSRF.
- Criterio: cumple HU-01; tests de `state` inválido, token inválido y sesión expirada.

**T1.2 Cifrado de campos.** Utilidad AES-256-GCM y tipo de columna cifrada.
- Criterio: en la base no aparece ningún valor en claro; ADR de rotación de clave.

**T1.3 Portafolio CRUD.** Alta, edición, baja y listado de posiciones, con RLS y validación contra el universo.
- Criterio: cumple HU-02; test de aislamiento entre dos usuarios.

**T1.4 Carga por CSV.** Plantilla descargable, validación por fila, límites de tamaño, rate limiting.
- Criterio: errores por fila; filas inválidas no se guardan.

**T1.5 Borrado de cuenta.** Borrado real de datos de usuario.

## Fase 2 — Universo y precios

**T2.1 Universo.** `config/universe.yaml` con 20 acciones del panel líder y 20 CEDEARs (ticker BYMA, ticker de origen, ratio, sector, CIK de la SEC si corresponde). Comando para sincronizarlo con la base.
- Selección: los más operados (volumen) del último trimestre cerrado, según la fuente más fiable disponible, preferentemente oficial (BYMA). La fuente, la ventana y la fecha de corte quedan documentadas en el YAML.

**T2.2 Interfaz `PriceProvider` e implementaciones** para BYMA Open Data y data912.
- Criterio: tests con respuestas grabadas (fixtures), sin llamadas reales en CI.

**T2.3 Tarea diaria de precios y tipo de cambio CCL**, con validación cruzada y marcado por divergencia.
- Criterio: idempotente; un dato marcado no aparece en informes.

## Fase 3 — Documentos y extracción

**T3.1 Discovery de fuentes (tarea de investigación).** Para cada empresa argentina del universo, documentar dónde y en qué formato publica estados contables y comunicados (relación con inversores, CNV). Guardar en `config/universe.yaml`. Lo que no se pueda resolver va a `specs/preguntas-abiertas.md`.
- Criterio: fuente documentada para al menos 10 empresas antes de seguir.
- Incluye los sitios de relación con inversores de las empresas cuyos comunicados no están en la CNV ni en la SEC (pregunta abierta 17).

**T3.2 Ingesta de documentos.** Descubrimiento, descarga, hash y almacenamiento desde la CNV (estados contables, reseñas y hechos relevantes), la SEC (6-K) y los sitios de inversores.

**T3.3 SEC XBRL.** Lectura de `companyfacts` y mapeo a métricas internas para los subyacentes de CEDEARs, con `SEC_USER_AGENT`.

**T3.4 Esquemas de extracción.** `FinancialStatementExtraction` y métricas por sector (`config/metrics_by_sector.yaml`), con moneda, unidad, período, base de medición y página fuente. Incluye el mapeo del plan de cuentas de la CNV a métricas internas.

**T3.5 Verificación triple de cifras** (pregunta abierta 18). Datos estructurados de la CNV, búsqueda literal en el texto del PDF (PyMuPDF) y extracción completa con LLM (con ruteo a modelo multimodal por página), más validaciones contables. Solo se publica lo que coincide en las tres; si no, estado `revision_manual`.
- Criterio: tests con documentos grabados donde cada tipo de diferencia termina en `revision_manual`.

**T3.6 Dataset de referencia y evals de extracción.** 10 empresas × 4 trimestres, cargado a mano; runner de evals con exactitud por campo, costo y latencia; comparación de al menos dos modelos, uno de ellos de bajo costo de otro proveedor (plan técnico, sección 9).
- **GATE:** no se amplía a las 40 empresas ni se pasa a la fase 5 sin cumplir los umbrales de la sección 9 del plan técnico.

**T3.7 Monitor de integraciones.** Registro de corridas por fuente y aviso por email a `ADMIN_EMAILS` cuando una fuente falla de forma repetida, cambia su formato o no publica un documento esperado. Nunca llega a clientes.
- Criterio: tests que simulan cada tipo de falla y verifican que se envía un único aviso por incidente.

## Fase 4 — Noticias y exposición

**T4.1 Taxonomía de factores** en `config/factors.yaml` (petróleo, gas, tipo de cambio, tasas en pesos, tasas en dólares, inflación, riesgo país, regulación energética, regulación financiera, retenciones agropecuarias, tarifas de servicios públicos, consumo, entre otros).

**T4.2 Propuesta del mapa de exposición** con LLM, con cita a documento y página; endpoint de administración y comando de CLI para aprobar o rechazar.
- Criterio: cumple HU-07; solo se usan entradas aprobadas.

**T4.3 Ingesta de noticias** desde `config/news_sources.yaml`, guardando solo título, link, fecha y resumen propio.

**T4.4 Deduplicación** por embeddings con pgvector.

**T4.5 Clasificación y ruteo** (empresas, importancia, factores) y sus evals, comparando al menos un modelo de bajo costo de otro proveedor (plan técnico, sección 9).
- **GATE:** umbrales de la sección 9 del plan técnico.

## Fase 5 — Señales e informes

**T5.1 Motor de señales** desde `config/signals.yaml`, con valores usados guardados, polaridad (`fuerte`/`debil`) y materialidad.

**T5.2 Sistema de marcadores y validación de texto** (sin dígitos fuera de marcadores, marcadores existentes, lenguaje prohibido).
- Criterio: tests con textos que deben ser rechazados.

**T5.3 Informe trimestral por empresa** (HU-04), generado una vez por empresa y período.
- Criterio: secciones de puntos fuertes y débiles siempre presentes, seleccionadas por código; test que verifica la simetría.

**T5.4 Resumen semanal por usuario** (HU-03), reutilizando bloques por empresa y noticia.

**T5.5 Evals de redacción** (30 informes; validaciones más juez de fidelidad).
- **GATE:** umbrales de la sección 9 del plan técnico.

**T5.6 Informe de ejemplo público** generado por el sistema para la landing (HU-05).

## Fase 6 — Envío y métricas

**T6.1 Envío por Resend** con plantilla HTML, link de baja en un clic (HU-08) y preferencias.

**T6.2 Webhooks de Resend** (con verificación de firma) para registrar aperturas.

**T6.3 Analytics de producto:** registros, intención de pago y tasa de apertura semanal.

**T6.4 Revisiones manuales (admin).** Sección de admin (API y panel web) que lista los documentos en `revision_manual` con las diferencias encontradas y las páginas del PDF, y permite aprobar el valor correcto o descartarlo. Solo rol `admin`; queda registrado quién aprobó y cuándo.

## Fase 7 — Frontend (`brujula-web`)

**T7.1** Proyecto Next.js con TypeScript estricto y Tailwind, `.env.example`.
**T7.2** Landing con informe de ejemplo, precios y disclaimer.
**T7.3** Ingreso con Google (vía API) y panel de portafolio con carga de CSV.
**T7.4** Vista de informes y preferencias de email.
**T7.5** Páginas legales.
**T7.6** Fake door del plan pago (HU-06), que registra el evento en la API (anónimo en `visitor_intents` o vinculado al usuario).

## Fase 8 — Despliegue

**T8.1** VPS con Docker Compose, Caddy con TLS y dominios `api.` y `app.`.
**T8.2** Backups diarios con prueba de restauración documentada.
**T8.3** Frontend en Vercel.
**T8.4** README con arquitectura, decisiones, tabla de resultados de evals (calidad, costo y latencia por modelo) e informe de ejemplo.

## Fase 9 — Validación y cobro

**T9.1** Definir `VALIDATION_THRESHOLD_PCT` y la métrica de éxito **antes** de la campaña.
**T9.2** Campaña de USD 50 y medición.
- **GATE:** solo si se supera el umbral, continuar con T9.3.

**T9.3** Suscripciones con Mercado Pago, webhooks con verificación de firma y activación del plan pago.
**T9.4** Revisión legal (asesoramiento, términos de uso de datos de precios, protección de datos) antes de cobrar.
