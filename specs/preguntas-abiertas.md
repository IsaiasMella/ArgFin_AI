# Preguntas abiertas

Dudas detectadas al leer las specs. No se inventan respuestas: se resuelven con el
responsable del producto y la decisión se lleva a la spec correspondiente.

## Pendientes

### 5. Intención de pago de visitantes sin registrarse (bloquea T6.3 y T7.6)

**Contexto.** La métrica de validación del MVP es "porcentaje de *visitantes* de la campaña
que eligen el plan pago" (`01-spec.md`, sección 7). Un visitante que llega desde el anuncio
y toca "Quiero el plan pago" en la página de precios **todavía no tiene cuenta**, así que no
hay `user_id` para asociar ese clic, y la política de RLS por `user_id` no aplica.

**Propuesta:** guardar esos clics en una tabla separada sin datos personales (identificador
aleatorio de visitante en una cookie propia, plan, fecha y campaña), donde la app solo puede
insertar. Si el visitante después se registra, se vincula con su usuario.

**Alternativa:** exigir registro antes de mostrar la intención de pago (más simple, pero
mide otra cosa: registrados, no visitantes).

**Idea nueva a definir:** se mencionó regalar al registrarse un informe gratis de una
empresa al azar del portafolio (distinto del trimestral y del resumen semanal). No está en
la spec: falta definir qué contiene, cuándo se envía y si es parte del MVP.

### 9. Énfasis según una tesis de compra o venta (bloquea T5.2 a T5.5)

**Contradice la constitución, punto 1** ("nunca recomienda… ni de forma explícita ni
implícita"). Se pidió resaltar lo positivo cuando "correspondería comprar" y lo negativo
cuando "correspondería vender", sin usar palabras de recomendación. Eso es una
recomendación implícita y además exige que el sistema decida una tesis, lo que el LLM no
puede hacer según la constitución. **No se implementa** salvo que se modifique la
constitución, con revisión legal previa (T9.4). Alternativa compatible: secciones
simétricas y siempre presentes de "puntos fuertes" y "puntos débiles", ordenadas por
reglas determinísticas de materialidad (señales de `config/signals.yaml`), sin tesis.

*La parte de flexiones verbales quedó resuelta: ver decisión 9 abajo.*

## Resueltas (2026-10-05)

1. **`spec/` → `specs/`.** Se renombró la carpeta para coincidir con `AGENTS.md` y el plan.
2. **Git.** El remoto existe en GitHub. GitFlow (`main`, `develop`, `feature/*`), nunca push
   directo a `main`, Conventional Commits en español, sin atribuciones a IA en los commits.
   Ver `AGENTS.md`, sección Git.
3. **Login con RLS.** Aprobadas funciones `SECURITY DEFINER` acotadas. ADR en T0.4.
4. **Worker que enumera usuarios.** Mismo mecanismo que el punto 3, devolviendo solo ids.
6. **Roles.** Columna `users.rol` con FK a un catálogo `roles` (extensible). El rol define
   permisos y es independiente del plan. `ADMIN_EMAILS` en `.env` asigna `admin` al ingresar.
7. **Planes.** Durante el MVP todos reciben todo: quien se registra con
   `FOUNDER_PLAN_OPEN=true` queda en plan `fundador` (todo gratis para siempre). Después:
   plan gratis con un resumen semanal por mes, hasta 5 posiciones y sin trimestrales; plan
   pago con todo. Ver `01-spec.md`, sección 6.
8. **Universo.** Los más operados del último trimestre cerrado, según la fuente más fiable,
   preferentemente oficial (BYMA). Se documenta fuente y fecha de corte en T2.1.
9. **Flexiones de palabras prohibidas.** Se bloquean formas de recomendación (infinitivo,
   imperativo, "conviene", "habría que", segunda persona), no hechos en tercera persona
   ("la empresa vendió su participación").
10. **Dígitos en nombres y períodos.** Lista blanca versionada en
    `config/numeros_permitidos.yaml` ("3M", "G20", "COVID-19", "2T26", "Ley 25.326").
11. **Cifras de noticias.** Se extraen como datos con fuente (`news_facts`) y se usan con
    marcadores; el valor debe aparecer literalmente en el texto fuente.
12. **Zona horaria.** Variable `APP_TIMEZONE` (para Argentina,
    `America/Argentina/Buenos_Aires`). Sin valor por defecto en el código.
13. **Almacenamiento de PDFs.** Disco del VPS (`DOCUMENT_STORAGE_DIR`) detrás de una interfaz
    `DocumentStorage`, incluido en los backups. Migrable a almacenamiento tipo S3.
14. **Embeddings.** OpenAI `text-embedding-3-large`, acortado a `LLM_EMBEDDING_DIMENSIONS`
    (≤ 2000 por el límite de índices HNSW de pgvector). Más dimensiones no implica
    automáticamente más precisión; se confirma con las evals de T4.4.
