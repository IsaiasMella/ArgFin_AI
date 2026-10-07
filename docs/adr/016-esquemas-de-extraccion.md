# 016 — Esquemas de extracción y mapeo de la CNV

- **Estado:** aceptada
- **Fecha:** 2026-10-07

## Contexto

T3.4 pide el esquema `FinancialStatementExtraction` y las métricas obligatorias por sector,
con moneda, unidad, período, base de medición y página fuente. Por la decisión de la
pregunta 18, las cifras de las empresas argentinas salen de los datos estructurados de la
CNV y se verifican contra el PDF y contra una extracción con LLM (T3.5). Este esquema y el
mapeo de cuentas son la base de esa verificación.

## Decisión

1. **Plan de cuentas de la CNV a métricas** (`config/cnv_accounts.yaml`):
   - **Reglas por código y por patrón sobre el rubro.** El mismo código significa cosas
     distintas según la plantilla (relevado sobre las 20 empresas):
     - **3000100** es "Ingresos de actividades ordinarias" en industria e "Ingresos por
       intereses" en bancos.
     - **3009999** es "Ganancia bruta" o "Ingreso operativo neto".
     - **3241100**, en industria, es "Total cambios en activos y pasivos operativos", no el
       flujo operativo.
   - **Una regla puede sumar cuentas,** por ejemplo la deuda financiera, corriente más no
     corriente.
   - **Rubros normalizados:** mayúsculas, sin tildes y con espacios simples.
2. **Montos estrictos:**
   - Se aceptan `329308168.00`, `25756262378` y `43.346.287` (puntos de miles).
   - `1.234` (un solo punto y tres decimales) es ambiguo: se informa como error y no se
     adivina.
   - `-` o vacío significa "sin saldo" y la métrica no se informa.
3. **Unidades:** los importes se llevan a unidades con la `UnidadMedida` de la presentación
   (`$`, `Miles de $`, `Millones de $`). Una unidad desconocida no mapea nada. La ganancia
   por acción no se escala.
4. **Base de medición:** los estados argentinos están en **moneda homogénea** (NIC 29),
   reexpresados a la fecha de cierre. Se guarda `base_medicion = homogenea` y
   `fecha_reexpresion = cierre` (T3.5).
5. **Períodos:** los flujos de un estado trimestral son **acumulados del ejercicio**. Su
   inicio es el día siguiente al cierre del ejercicio anterior
   (`cnv.cierre_ejercicio`: Aluar cierra el 30/06).
6. **Métricas obligatorias por sector** (`config/metrics_by_sector.yaml`):
   - Comunes a todos: activo, pasivo, patrimonio y resultado neto.
   - Por sector: ingresos y resultado operativo donde corresponde.
   - "Financiero" mezcla bancos con BYMA y Banco de Valores, que no son bancos comerciales.
     Por eso solo exige las comunes.
   - La carga valida que todos los sectores del universo estén.
7. **`FinancialStatementExtraction`** (structured output del LLM en T3.5):
   - **Cabecera:** moneda, unidad (unidades, miles o millones), base de medición, fecha de
     cierre y tipo de balance.
   - **Por cifra:** métrica del catálogo, valor en la unidad del documento, **el número tal
     como está impreso**, página, período y si es comparativo.
   - **El texto impreso permite verificar con código** que el número está en esa página.

## Resultado sobre datos reales (último estado de cada una de las 20 empresas)

- **Métricas obligatorias:** ninguna empresa tiene faltantes. Se resuelven entre 16 y 19
  métricas por empresa.
- **Identidad activo = pasivo + patrimonio:** cierra exacta en 19 de 20.
  - **Supervielle no cierra por 410 (miles de $):** su patrimonio declarado tiene
    decimales que no cuadran.
  - Es justo lo que debe detectar la validación de T3.5, con tolerancia configurable.

## Alternativas consideradas

- **Mapear solo por código:** confunde ingresos con intereses en los bancos.
- **Mapear solo por rubro:** los rubros varían en mayúsculas y espacios, y a veces los
  códigos son la única referencia estable. Se usan los dos.
- **Interpretar "1.234" según el contexto:** sería adivinar. Va a revisión.
