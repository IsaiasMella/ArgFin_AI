# 015 — Cifras de los CEDEARs desde el XBRL de la SEC

- **Estado:** aceptada
- **Fecha:** 2026-10-07

## Contexto

T3.3 pide leer `companyfacts` de la SEC y mapear sus conceptos a métricas internas para los
subyacentes de los CEDEARs, sin LLM (plan 7.2.3). Las cifras tienen que poder rastrearse
hasta su origen (constitución, punto 2).

## Decisión

1. **Catálogo único de métricas internas** (`config/metrics.yaml`):
   - Cada métrica tiene código, nombre, tipo (`saldo` o `flujo`) y unidad (`moneda`,
     `moneda_por_accion` o `acciones`).
   - Todas las fuentes se traducen a ese catálogo: SEC, CNV (T3.4) y PDF (T3.5).
2. **Mapeo determinístico** (`config/sec_xbrl.yaml`): por métrica, una lista de conceptos
   (`us-gaap:` para las empresas de EE. UU. e `ifrs-full:` para las extranjeras) en orden
   de preferencia.
   - **Mismo período en varios conceptos:** gana el de mayor preferencia que tenga dato.
   - **Mismo concepto en varias presentaciones** (el original y luego como comparativo,
     quizá reexpresado): gana la presentación más reciente.
   - **Una sola moneda por empresa:** la más frecuente en sus datos, que es la de sus
     estados.
   - **Se guardan los flujos trimestrales y los acumulados del ejercicio,** diferenciados
     por `periodo_inicio`.
3. **Tabla `financial_facts`:** una fila por empresa, métrica, período y fuente (unicidad
   con `NULLS NOT DISTINCT`, porque los saldos no tienen inicio). Cada fila trae:
   - `referencia`: concepto y accession;
   - `formulario`;
   - `fecha_presentacion`;
   - `extractor_version`.
4. **Idempotente:** upsert que solo escribe si cambió algo. Una reexpresión actualiza el
   valor.
5. **Ejecución:**
   - Tarea `cifras:sec` con `CRON_FILINGS_CHECK`, la misma agenda que la ingesta de
     documentos.
   - Comando `python -m brujula.cli actualizar-sec [--empresa CLAVE]`.
   - Identificación con `SEC_USER_AGENT`.

## Limitaciones encontradas (datos reales del 2026-10-07)

- **Empresas que presentan 20-F en NIIF (Nu, Vista y Nebius):**
  - En XBRL solo publican datos **anuales**. Sus trimestres van por 6-K, sin XBRL.
  - **`companyfacts` todavía no incluye sus 20-F de abril de 2026,** aunque esos 20-F
    tienen XBRL en línea. El último ejercicio disponible es 2024 (Nu y Vista).
  - **Nebius** casi no tiene conceptos de `ifrs-full` (2 cifras).
- **Las 17 empresas con 10-Q/10-K** tienen entre 240 y 310 cifras desde 2024, con el
  último trimestre presentado.

**Mitigación posterior:** para las tres, leer el XBRL en línea del 20-F directamente o
usar la verificación triple sobre sus comunicados (T3.5). Queda como mejora.

## Alternativas consideradas

- **API `frames` de la SEC (un concepto para todas las empresas):** sirve para comparar,
  pero no trae los acumulados ni las reexpresiones de una empresa.
- **Proveedores comerciales de fundamentals:** tienen costo y términos restrictivos. La SEC
  es la fuente oficial y gratuita.
- **Extraer los 10-Q con LLM:** innecesario, porque el XBRL ya es estructurado y oficial.
