# 017 — Verificación triple de los estados contables argentinos

- **Estado:** aceptada
- **Fecha:** 2026-10-08

## Contexto

T3.5 pide verificar las cifras antes de publicarlas. Según la decisión de la pregunta 18, cada
cifra de una empresa argentina tiene que coincidir en tres fuentes:

- los **datos estructurados de la CNV**;
- el **número impreso en el PDF firmado**;
- una **extracción completa con LLM**.

Si no coinciden, el documento va a revisión manual y no genera informe. Las cifras de la SEC
(ADR 015) ya llegan estructuradas desde XBRL auditado y no pasan por este flujo.

## Decisión

1. **Las tres fuentes son independientes:**
   - **CNV:** `cnv_mapping` (ADR 016).
   - **PDF:** el número de la CNV tiene que aparecer impreso tal cual en alguna página.
     - Se buscan las formas argentina (`1.234.567`) e inglesa (`1,234,567`), con y sin
       paréntesis o signo menos.
     - Si el PDF está en otra unidad, también se busca reescalado (la CNV en pesos y el PDF en
       miles).
     - Un número reescalado de menos de 4 dígitos no prueba nada, porque aparece por azar.
   - **LLM:** extrae **a ciegas**, sin ver los valores de la CNV, para que una coincidencia
     signifique algo.
     - Devuelve, por cifra, el número **tal como está impreso** y la página.
     - El código comprueba que ese texto esté en esa página. Si no está, la cifra del LLM se
       descarta (`llm_no_verificable`). El LLM tampoco se cree por sí solo.
2. **Regla por métrica:** una cifra se publica si se cumplen las dos condiciones:
   - el LLM la confirma, es decir, transcribe el mismo valor y el texto está en la página
     que cita;
   - hay **prueba impresa**: el literal de la CNV está en el PDF o lo está el impreso del LLM.
3. **Qué bloquea el documento:**
   - **Bloquean** (`bloqueante = true`, el documento va a `revision_manual` y no se publica
     nada):
     - una diferencia en una **métrica obligatoria del sector**
       (`config/metrics_by_sector.yaml`);
     - un **control global**: moneda desconocida, base de medición o fecha de cierre
       distintas, identidad contable, signos imposibles o saltos absurdos contra el período
       anterior verificado.
   - **No bloquean:** una diferencia en una métrica **opcional** (resultado bruto, flujos,
     ganancia por acción). Esa cifra no se publica y la diferencia queda registrada.
   - **Por qué:** muchos subtotales del formulario de la CNV no están impresos en los estados
     firmados (ver resultados). Si bloquearan, casi ningún documento se validaría.
4. **Tolerancia 0:**
   - Entre la CNV y el LLM solo se admite el redondeo de la unidad del documento: un PDF en
     miles puede diferir en menos de mil.
   - Un porcentaje, por chico que sea, dejaría pasar `450.132` por `450.123`.
   - La identidad activo = pasivo + patrimonio admite 0,0001 % (`tolerancia_identidad_pct`),
     por los decimales que algunas empresas cargan en la CNV.
5. **PDF escaneados:**
   - Una página con menos de `min_caracteres_texto` caracteres se considera escaneada.
   - Se lee con **OCR (Tesseract, español, 300 dpi)**, en un hilo aparte para no bloquear el
     worker, y además va **como imagen** al modelo multimodal.
   - Las imágenes van numeradas para que el LLM cite la página correcta.
   - El OCR solo sirve para buscar el número impreso. Si lee mal un dígito, la cifra no se
     verifica y va a revisión. Nunca se publica algo "parecido".
6. **Páginas para el LLM:**
   - Se envían las de los estados principales (por título) y las páginas donde aparecen los
     números de la CNV, hasta `max_paginas_llm`.
   - No se envía el PDF entero, que llega a 150 páginas: eso cuesta y distrae.
7. **El EBITDA no se toma de la CNV:** es un indicador que la empresa carga en el
   formulario y no figura en los estados firmados, así que no se puede verificar. Si un
   informe lo necesita, el código lo calcula a partir de cifras verificadas.
8. **Persistencia** (migración 0010):
   - **Diferencias:** cada una se guarda en `verification_issues` con motivo, métrica,
     valores, página, modelo y si es bloqueante. Es la base de la sección de admin
     "Revisiones manuales" (T6.4).
   - **Documento:** guarda `verificado_en` y `costo_verificacion_usd`.
   - **Cifras publicadas:** quedan con `fuente = cnv`, `base_medicion = homogenea`,
     `fecha_reexpresion` = cierre, la página del PDF y `extractor_version = cnv@1+<modelo>`.
9. **Fallas:**
   - Si el LLM no responde (presupuesto agotado o proveedor caído), no hay diferencia: el
     documento sigue `descargado` y se reintenta.
   - Un documento en `revision_manual` no se vuelve a enviar al LLM: no se gasta dos veces en
     lo mismo.
   - Solo se verifica la **última presentación** de cada período, porque las anteriores
     quedan reemplazadas.
10. **Configuración:**
    - Tolerancias, páginas, OCR y monedas: `config/extraction.yaml`.
    - Prompt versionado: `prompts/extraer_estado_contable@1.yaml`.
    - Modelo: `LLM_EXTRACTION_MODEL`. Se puede cambiar por corrida con
      `verificar-estados --modelo` (para T3.6).
    - Horario: `CRON_FILINGS_CHECK`, la tarea periódica `cifras:verificar`.

## Resultados sobre datos reales (2026-10-08)

- **Literal en el PDF:** de 661 cifras de la CNV (último estado de las 20 empresas), 440
  aparecen impresas tal cual en el PDF con texto. Las que faltan son, sobre todo:
  - **subtotales que la empresa calcula para el formulario** y no imprime: resultado
    bruto, resultado operativo, deuda financiera y depreciaciones;
  - **ganancias por acción** con otro redondeo.
  Por eso solo los totales que todos imprimen son obligatorios: activo, pasivo, patrimonio,
  resultado neto y, fuera del sector financiero, ingresos.
- **PDF totalmente escaneados:** cinco empresas (ALUAR, BANCO_VALORES, CENTRAL_PUERTO, ECOGAS
  y TELECOM) presentan los estados como imagen. Cifras encontradas con OCR:

  | Empresa y cierre | Cifras encontradas |
  |---|---|
  | Central Puerto 03-31 | 17/17 |
  | Central Puerto 06-30 | 17/17 |
  | Banco de Valores | 14/17 |
  | Aluar 06-30 | 15/18 |
  | Aluar 03-31 | 3/18 |

  - El escaneo de Aluar 03-31 es de baja calidad: el OCR lee `3.566,260.507,216` donde dice
    `3.566.260.807.216`. Ese documento va a revisión manual, que es lo correcto.
  - El OCR tarda unos 3 segundos por página: entre 80 y 110 segundos por documento.
- **LLM:** la verificación con un proveedor real **queda pendiente** hasta que estén cargadas
  las claves y los modelos en `.env`. Los tests usan un extractor simulado. El costo por
  documento queda registrado en `costo_verificacion_usd` y en `llm_calls` (ADR 005).

## Alternativas consideradas

- **Bloquear ante cualquier diferencia:** no se validaría casi ningún documento, porque los
  subtotales del formulario no están impresos. La regla por métrica conserva el rigor
  (nada se publica sin las tres fuentes) sin descartar lo que sí se pudo verificar.
- **Darle al LLM los valores de la CNV para que los "confirme":** sesga la respuesta hacia
  "sí" y deja de ser una fuente independiente.
- **Creerle al LLM sin comprobar el texto impreso:** un LLM puede alucinar un número
  plausible. Exigir el texto en la página lo vuelve verificable con código.
- **Solo imágenes para los escaneados, sin OCR:** no habría forma de comprobar con código
  que el número está impreso, así que se perdería la prueba literal.
- **Tolerancia porcentual:** deja pasar dígitos distintos en cifras grandes.
