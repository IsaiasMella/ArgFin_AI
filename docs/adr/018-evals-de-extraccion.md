# 018 — Dataset de referencia y evals de extracción

- **Estado:** aceptada
- **Fecha:** 2026-10-08

## Contexto

T3.6 pide:

- un dataset de referencia de 10 empresas × 4 trimestres, cargado y verificado a mano;
- un runner con exactitud por campo, costo y latencia;
- la comparación de al menos dos modelos, uno de ellos de bajo costo y de otro proveedor.

El plan técnico (sección 9) fija el GATE: **≥ 95 % de campos correctos (±0,5 %) y 100 % de
moneda, unidad y base correctas**. Sin cumplirlo no se amplía a 40 empresas ni se pasa a la
fase 5.

## Decisión

1. **El dataset se precarga y una persona lo verifica**
   (`evals/datasets/estados_contables.yaml`):
   - `python -m evals.runners.extraction preparar` toma, para cada empresa, los últimos 4
     cierres (el consolidado).
   - Precarga solo las cifras de la CNV que **también están impresas en el PDF**. Coinciden
     dos fuentes independientes y se guardan la página y el texto impreso, para verificar
     rápido.
   - Cada caso queda **pendiente** (`verificado_por: null`) hasta que una persona lo
     compruebe contra el PDF y complete `"Nombre, AAAA-MM-DD"`.
   - `preparar` **nunca pisa un caso verificado**.
   - No se precargan las métricas que suman cuentas (su total no suele estar impreso) ni los
     números reescalados de menos de 4 dígitos (aparecen por azar).
   - Cada caso fija el PDF por su SHA-256: si la empresa vuelve a presentar, el caso falla
     con "documento no encontrado" en lugar de evaluar contra otro documento.
2. **El runner usa el mismo camino que producción** (`python -m evals.runners.extraction
   correr`):
   - Usa `StatementVerifier.prepare` (OCR y selección de páginas) y
     `StatementVerifier.extract` (prompt versionado y modelo).
   - Así se mide lo que de verdad corre en T3.5, no una variante.
   - Cada documento se prepara una sola vez y se envía a todos los modelos.
3. **Métricas:**
   - **Cifras correctas:** dentro de `tolerancia_pct` (0,5 %) del valor de referencia, en
     unidades.
   - **Cabecera correcta:** moneda, unidad y base de medición.
   - **Costo y latencia promedio por caso:** el costo sale del cliente LLM (precios de
     LiteLLM al momento de correr) y la latencia se mide alrededor de la extracción.
   - **Errores:** si el LLM no responde, el caso cuenta con todas sus cifras mal. Así un
     modelo que falla no queda mejor.
4. **GATE:**
   - Se evalúa **solo con el dataset completo** (10 empresas y 40 casos) y **todos los casos
     verificados a mano**.
   - Si no, el resultado es "no evaluable", aunque las cifras den bien. Coincidir con la CNV
     no reemplaza la verificación humana que pide el plan.
   - Umbrales en `config/evals.yaml`.
5. **Elección de modelo:**
   - Por defecto se comparan `LLM_EXTRACTION_MODEL` y `LLM_EXTRACTION_FALLBACK_MODEL`. Con
     `--modelo` (repetible) se prueba cualquier otro, siempre que su proveedor tenga clave en
     `.env`.
   - El runner informa el **más barato que cumple los umbrales**.
6. **Resultados** (`evals/results/extraccion/`):
   - **Por modelo:** un JSON por fecha, modelo y versión de prompt, con el resumen, el
     detalle de cada caso y el SHA-256 del dataset.
   - **Comparación:** una tabla Markdown para el README.
   - Se versionan en git.
7. **Dónde corre:**
   - **No en CI:** cuesta dinero (ADR 006) y necesita los PDF descargados, que no están en el
     repositorio. Se corre a mano en el entorno con los documentos.
   - **Fuera de la imagen de producción:** `evals/` sigue excluida del contexto de build. Se
     monta desde el repositorio (`-v ./evals:/app/evals`), que es donde se escriben el
     dataset y los resultados.

## Estado al cerrar T3.6

- **Dataset precargado:** 10 empresas × 4 trimestres. Todos los casos están **pendientes de
  verificación humana**.
- **Eval contra modelos reales:** no se corrió, porque faltan las claves y los modelos en
  `.env`.
- **El GATE no está cumplido** y no se puede declarar hasta tener las dos cosas.

## Alternativas consideradas

- **Cargar el dataset 100 % a mano desde cero:** son unas 600 cifras. Precargar con dos
  fuentes coincidentes y verificar es más rápido y comete menos errores de tipeo.
- **Usar la CNV como verdad sin verificación humana:** el plan exige verificación a mano, y
  la CNV tiene errores propios (por ejemplo, la identidad de Supervielle).
- **Un runner separado del pipeline:** mediría algo distinto de lo que corre en producción.
