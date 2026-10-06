# 01 — Especificación de producto (MVP)

## 1. Problema

El inversor minorista argentino suele tener un portafolio repartido entre acciones argentinas y CEDEARs, a veces en varios brokers. El research de calidad existe y es gratuito (los brokers lo usan como marketing), pero está **disperso**: cada broker cubre pocas empresas, cuando quiere, y nadie lo organiza según lo que **cada inversor** tiene. Seguir el propio portafolio lleva horas y es fácil perderse algo importante.

## 2. Propuesta de valor

> "Todo lo que le pasó a **tu** portafolio esta semana, en 3 minutos, en español, con la fuente de cada dato."

Se paga por **ahorrar tiempo y no perderse nada importante**, no por recomendaciones.

## 3. Usuario objetivo

Inversor minorista argentino, de largo plazo, con 5 a 20 posiciones entre acciones argentinas y CEDEARs. No es trader: no necesita tiempo real.

## 4. Alcance del MVP

### 4.1 Universo cubierto

- **20 acciones del panel líder argentino** y **20 CEDEARs más operados**, definidos en `config/universe.yaml`.
- Posiciones fuera del universo: se aceptan, pero solo se informa precio y variación (sin informe profundo), indicándolo claramente.

### 4.2 Funcionalidades incluidas

1. **Login con OAuth 2.0 / OpenID Connect (Google).**
2. **Carga del portafolio:** manual y por CSV (con plantilla descargable).
3. **Precios de cierre diarios** de todo el universo, con validación cruzada entre dos fuentes.
4. **Ingesta y extracción de estados contables:** PDFs de empresas argentinas y datos XBRL de la SEC para los subyacentes de los CEDEARs.
5. **Mapa de exposición** por empresa: factores macro que la afectan, propuestos por IA y aprobados por un humano.
6. **Ingesta y clasificación de noticias:** relevancia por empresa, importancia, deduplicación y asignación a factores del mapa de exposición.
7. **Motor de señales** determinísticas sobre métricas.
8. **Resumen semanal** del portafolio por email.
9. **Informe trimestral por empresa**, disparado cuando se publica un nuevo estado contable.
10. **Landing con informe de ejemplo público, precios y prueba de intención de pago** (fake door). Cobro real con Mercado Pago solo si se supera el umbral de validación (ver fase 9 de tareas).
11. **Métricas de producto:** registros, intención de pago, apertura de emails.

### 4.3 Fuera del MVP

Todo lo listado en `ROADMAP.md`. En particular: alertas inmediatas, regulación CNV/BCRA, acciones de EE.UU. directas, importación desde brokers, separación del rendimiento de CEDEARs, eventos corporativos completos.

## 5. Historias de usuario y criterios de aceptación

Los criterios usan formato EARS ("CUANDO… ENTONCES el sistema…").

### HU-01 Ingresar
Como inversor, quiero ingresar con mi cuenta de Google para no crear otra contraseña.
- CUANDO el usuario elige "Ingresar con Google" ENTONCES el sistema inicia un flujo OAuth 2.0 con PKCE y `state`.
- CUANDO el flujo termina bien ENTONCES el sistema crea la sesión en una cookie `HttpOnly`, `Secure`, `SameSite=Lax`.
- CUANDO el `state` no coincide o el token es inválido ENTONCES el sistema rechaza el ingreso y registra el evento.
- CUANDO el usuario cierra sesión ENTONCES la sesión se invalida en el servidor.

### HU-02 Cargar mi portafolio
Como inversor, quiero cargar mis posiciones a mano o con un CSV.
- CUANDO el usuario carga un ticker ENTONCES el sistema lo valida contra el universo e indica si tiene cobertura completa o solo de precio.
- CUANDO el usuario sube un CSV ENTONCES el sistema valida cada fila y muestra los errores por fila sin guardar filas inválidas.
- El sistema nunca pide credenciales de brokers.
- Cantidades y precio promedio se guardan cifrados.

### HU-03 Recibir el resumen semanal
Como inversor, quiero un email semanal con lo importante de mi portafolio.
- CUANDO se cumple el horario configurado ENTONCES el sistema envía a cada usuario activo un resumen con: valor del portafolio y variación semanal (en pesos y en dólares CCL), variación por posición, hechos de la semana por empresa, noticias macro que afectan factores a los que sus empresas están expuestas, y próximos eventos (por ejemplo, fechas de presentación de resultados).
- Cada hecho o noticia incluye link a la fuente.
- Ninguna cifra del email es generada por un LLM (ver constitución, punto 2).

### HU-04 Recibir el informe trimestral
Como inversor, quiero un informe cuando una de mis empresas presenta resultados.
- CUANDO se ingiere y valida un nuevo estado contable de una empresa del universo ENTONCES el sistema genera **un** informe para esa empresa y lo envía a todos los usuarios que la tienen.
- El informe contiene: encabezado con datos de mercado, resumen del trimestre, tabla de métricas (trimestre actual contra el anterior y contra el mismo trimestre del año anterior), secciones por tema, **puntos fuertes y puntos débiles** (siempre ambas secciones, derivadas de las señales activadas), exposición a factores, fuentes y disclaimer.
- No contiene recomendación ni precio objetivo.

### HU-05 Ver un informe de ejemplo
Como visitante, quiero ver un informe real antes de registrarme.
- La landing muestra un informe de ejemplo completo, generado por el propio sistema.

### HU-06 Mostrar intención de pago
Como visitante, quiero ver precios claros.
- La página de precios muestra plan gratuito y plan pago.
- CUANDO el visitante elige el plan pago ENTONCES el sistema registra el evento de intención (fake door) y le explica con honestidad que el plan pago abre pronto.
- CUANDO el visitante no tiene cuenta ENTONCES el clic se registra de forma anónima (sin datos personales) y se lo invita a registrarse para entrar en la lista de espera; si se registra, la intención se vincula a su usuario.

### HU-07 Administrar el mapa de exposición (administrador)
Como administrador, quiero revisar y aprobar los factores de exposición propuestos por IA.
- Cada propuesta incluye factor, dirección, intensidad y justificación con cita al documento.
- Solo los factores aprobados se usan en informes.

### HU-08 Darme de baja
- Todo email incluye un link para desuscribirse en un clic.
- El usuario puede borrar su cuenta y sus datos.

## 6. Planes (hipótesis a validar)

| Plan | Incluye | Precio |
|---|---|---|
| Gratis | Un resumen semanal por mes (`FREE_PLAN_DIGESTS_PER_MONTH`), hasta 5 posiciones (`FREE_PLAN_MAX_POSITIONS`), sin informes trimestrales | $0 |
| Pago | Posiciones ilimitadas, todos los resúmenes semanales e informes trimestrales | Definido en `.env` (`PRICE_PRO_ARS`) |
| Fundador | Todo lo del plan pago, gratis y para siempre | $0 |

- **Durante el MVP todos reciben todo** (resúmenes semanales e informes trimestrales): todo usuario que se registra mientras `FOUNDER_PLAN_OPEN=true` queda con plan `fundador`.
- Al cerrar la validación se pone `FOUNDER_PLAN_OPEN=false`: los fundadores conservan todo gratis y los nuevos usuarios entran en el plan gratuito.
- El plan (qué funcionalidades recibe) es independiente del **rol** (qué puede hacer en el sistema, por ejemplo `admin`); ver `02-plan-tecnico.md`, sección 5.

## 7. Métricas de éxito del MVP

- **Validación de negocio:** porcentaje de visitantes de la campaña que eligen el plan pago. El umbral se define **antes** de lanzar la campaña de USD 50 y se guarda en `.env` (`VALIDATION_THRESHOLD_PCT`).
- **Retención:** tasa de apertura del resumen semanal.
- **Calidad técnica:** ver umbrales de evals en `02-plan-tecnico.md`.

## 8. Requisitos no funcionales

- Informes semanales generados y enviados en menos de 30 minutos para todo el padrón.
- Costo de IA medido por documento y por informe; tope mensual configurable con alerta.
- Disponibilidad razonable para un MVP (un solo VPS, backups diarios).
- Cumplimiento de la Ley 25.326 de protección de datos personales (pendiente de revisión legal).
