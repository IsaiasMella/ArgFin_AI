# Preguntas abiertas

Dudas detectadas al leer las specs. No se inventan respuestas: se resuelven con el
responsable del producto y la decisión se lleva a la spec correspondiente.

## Pendientes

15. **Precios de las posiciones fuera del universo** (detectada en T2.3, 2026-10-07). La spec
    (4.1) dice que de esas posiciones "solo se informa precio y variación", pero el plan
    (7.1) y T2.3 piden precios solo "para cada instrumento del universo". Hoy la tarea diaria
    cubre el universo y el CCL; los tickers libres no tienen precio.
    - **Recomendación:** sumarlos cuando se arme el resumen semanal (fase 5), que es el
      primero que los usa. La tarea diaria los leería con una función `SECURITY DEFINER`
      que devuelve solo la lista de tickers distintos, sin usuarios (mismo mecanismo que
      los puntos 3 y 4), y guardaría sus cierres en una tabla aparte. Un ticker libre no
      tiene tipo conocido: BYMA no lo necesita y data912 se probaría como acción y como
      CEDEAR.
    - **Necesito:** tu OK para ese enfoque, o adelantarlo a la fase 2 si preferís.
16. **Eventos corporativos y la historia de precios** (detectada en T2.3, 2026-10-07). BYMA
    ajusta su serie histórica hacia atrás ante splits y otros eventos (YPFD tuvo un split
    10:1 a fines de julio de 2026), mientras que data912 da los precios tal como se
    operaron. El `ROADMAP.md` deja los eventos corporativos completos fuera del MVP.
    - **Qué hace hoy el sistema (ADR 012):** marca los cierres afectados. Así ningún
      informe calcula una variación a través del evento, y en su lugar dice que no hay
      dato.
    - **Recomendación para el MVP:** mantenerlo así. Sumar al panel de admin (fase 6) una
      acción "reprocesar instrumento": vuelve a bajar de BYMA toda la serie ajustada y
      desmarca los cierres después de la revisión.
    - **Necesito:** tu OK, o que me digas si querés guardar también la serie sin ajustar
      (más trabajo y no lo pide la spec).
17. **Comunicados de resultados sin fuente uniforme** (T3.1, 2026-10-07). De las 20 empresas
    argentinas, 9 no publican sus comunicados ni en la CNV ni en la SEC: Transener, BYMA,
    Metrogas, Ternium Argentina, TGN, Banco de Valores, Ecogas, Comercial del Plata y Aluar.
    Detalle en el ADR 013.
    - **Recomendación:** que el informe trimestral se base en el estado contable (que la CNV
      tiene para las 20) y use el comunicado solo cuando haya fuente. No conviene
      integrar 9 sitios de inversores distintos para el MVP.
    - **Necesito:** tu OK.
18. **Usar los datos estructurados de la CNV para las cifras** (T3.1, 2026-10-07). La CNV
    publica, junto con cada estado contable, el plan de cuentas con sus montos: total del
    activo, pasivo y patrimonio, resultado, flujos, EBITDA y ganancia por acción. Lo verifiqué
    contra el PDF firmado. Eso permite leer las cifras principales con código, sin LLM, como
    con el XBRL de la SEC.
    - **Recomendación:** la estructura de la CNV, validada, es la fuente principal. El
      extractor de PDF con LLM (T3.5) queda para lo que la estructura no tiene (métricas por
      sector, segmentos) y para contrastar cuando viene incompleta. Ajusta el alcance de
      T3.4 a T3.6 y baja el costo por documento. Detalle en el ADR 013.
    - **Necesito:** tu OK para ajustar el plan técnico y las tareas en ese sentido.

## Resueltas (2026-10-05 y 2026-10-06)

1. **`spec/` → `specs/`.** Se renombró la carpeta para coincidir con `AGENTS.md` y el plan.
2. **Git.** El remoto existe en GitHub. GitFlow (`main`, `develop`, `feature/*`), nunca push
   directo a `main`, Conventional Commits en español, sin atribuciones a IA en los commits.
   Ver `AGENTS.md`, sección Git.
3. **Login con RLS.** Aprobadas funciones `SECURITY DEFINER` acotadas. ADR en T0.4.
4. **Worker que enumera usuarios.** Mismo mecanismo que el punto 3, devolviendo solo ids.
5. **Intención de pago de visitantes sin cuenta.** Tabla `visitor_intents` sin datos
   personales (id aleatorio en cookie propia); la app solo inserta. Si el visitante se
   registra, la intención se copia a `payment_intents` de su usuario. El informe gratis de
   bienvenida queda para después del MVP (`ROADMAP.md`, versión 1.1).
6. **Roles.** Columna `users.rol` con FK a un catálogo `roles` (extensible). El rol define
   permisos y es independiente del plan. `ADMIN_EMAILS` en `.env` asigna `admin` al ingresar.
7. **Planes.** Durante el MVP todos reciben todo: quien se registra con
   `FOUNDER_PLAN_OPEN=true` queda en plan `fundador` (todo gratis para siempre). Después:
   plan gratis con un resumen semanal por mes, hasta 5 posiciones y sin trimestrales; plan
   pago con todo. Ver `01-spec.md`, sección 6.
8. **Universo.** Los más operados del último trimestre cerrado, según la fuente más fiable,
   preferentemente oficial (BYMA). Se documenta fuente y fecha de corte en T2.1.
9. **Puntos fuertes y débiles, sin tesis.** No se resalta lo positivo o lo negativo según una
   tesis de compra o venta (sería una recomendación implícita). Todo informe tiene secciones
   simétricas de puntos fuertes y débiles, elegidas y ordenadas por reglas determinísticas
   (polaridad y materialidad en `config/signals.yaml`). Quedó en la constitución, punto 1.
   Ofrecer recomendaciones con un asesor registrado en la CNV se evalúa más adelante
   (`ROADMAP.md`, versión 2).
   **Flexiones de palabras prohibidas.** Se bloquean formas de recomendación (infinitivo,
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
