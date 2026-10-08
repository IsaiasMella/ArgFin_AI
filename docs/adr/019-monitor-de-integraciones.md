# 019 — Monitor de integraciones

- **Estado:** aceptada
- **Fecha:** 2026-10-08

## Contexto

Según la pregunta 17, el producto integra todas las fuentes: CNV, SEC y sitios de inversores.
Esas integraciones son frágiles, porque un sitio cambia su HTML o una API se cae. T3.7 pide:

- registrar las corridas por fuente;
- avisar **solo a `ADMIN_EMAILS`** cuando una fuente falla de forma repetida, cambia su
  formato o no publica un documento esperado;
- **un único aviso por incidente**;
- que nunca le llegue nada a un cliente.

## Decisión

1. **Cada integración informa sus corridas** con `SourceRun` (`core/integrations.py`):
   fuente, objeto (empresa o proveedor), motivo y si es un problema de formato.
   - **Documentos** (`documentos:buscar`): una corrida por empresa y fuente (`cnv`,
     `cnv_hechos`, `sec`, `sitio`).
   - **Cifras de la SEC** (`cifras:sec`): una por empresa (`sec_xbrl`).
   - **Precios** (`precios:actualizar_diario`): una por proveedor (`precios:byma`, `precios:data912`).
     Un proveedor falla solo si **no trajo ninguna especie**: una especie suelta sin datos
     no es una falla de la integración. También se registra cuando la corrida se aborta
     porque ninguna fuente respondió.
2. **Falla o cambio de formato:** `core/http.FormatError`, subclase de `FetchError`,
   distingue "la fuente respondió con algo que no entendemos" de "no respondió".
   - Las fuentes lo lanzan cuando el JSON, el XML o la estructura no son los esperados:
     `CnvFormatError`, `sec_formato_inesperado`, `wordpress_formato_inesperado`.
   - En precios, los motivos `formato_inesperado`, `respuesta_no_json` y
     `valor_no_numerico` cuentan como formato.
3. **Cuándo se abre un incidente** (`config/monitor.yaml`):
   - **Falla repetida:** `fallas_consecutivas` (3) corridas seguidas con error para la
     misma fuente y objeto. Una falla suelta suele ser transitoria, y una corrida bien en el
     medio corta la racha.
   - **Formato:** a la primera, porque no se arregla reintentando.
   - **Documento faltante:** el último estado contable cuyo plazo legal ya venció, más un
     margen, no está en la CNV.
     - **Plazos** (Normas de la CNV, Título IV): 70 días corridos para el anual y 42 para
       los intermedios.
     - **Margen:** 5 días, por feriados y demoras de la AIF.
     - Se calcula con el cierre de ejercicio de cada empresa (`cnv.cierre_ejercicio`).
       Aluar cierra el 30/06.
4. **Único aviso por incidente:**
   - Un índice único parcial (`tipo, fuente, objeto` con `cerrado_en IS NULL`) impide
     tener dos incidentes abiertos iguales.
   - `avisado_en` marca qué ya se avisó.
   - Los incidentes nuevos de una corrida van **en un solo email**.
   - Un incidente se **cierra solo**: cuando la fuente vuelve a responder bien o cuando el
     estado aparece. Si después vuelve a pasar, es un incidente nuevo con su propio aviso.
5. **Email:**
   - Se envía por Resend (`core/email.py`) **solo a `ADMIN_EMAILS`**. El monitor no tiene
     acceso a los emails de los usuarios.
   - Si el envío falla, los incidentes quedan sin avisar y se reintenta en la próxima
     corrida.
   - El encabezado `Idempotency-Key`, que es el hash de los incidentes, evita un email
     duplicado si un reintento HTTP llega después de un envío que sí se hizo.
   - URL de la API: `RESEND_API_BASE_URL`, nueva en `.env`.
6. **El monitor nunca corta una ingesta:** si falla (por ejemplo, por la base), queda en el
   log con nivel error y la tarea de datos sigue (`report_runs_safely`).
7. **Retención:** las corridas se borran a los 90 días (`retencion_corridas_dias`). Los
   incidentes se conservan como historial.

## Alternativas consideradas

- **Avisar ante cada falla:** la CNV y los sitios tienen caídas cortas frecuentes, y cada
  una generaría un email. La racha de 3 filtra el ruido sin demorar demasiado: con
  `CRON_FILINGS_CHECK` cada 6 horas, son unas 18 horas.
- **Un email por incidente:** si se cae la CNV, llegarían 20 emails, uno por empresa. Se
  agrupan los incidentes nuevos de cada corrida.
- **Avisar también cuando se resuelve:** no lo pide la tarea y duplica el volumen. La
  resolución queda registrada en `cerrado_en`, y la sección de admin (T6.4) la puede
  mostrar.
- **Monitorear desde fuera, con un servicio de uptime:** no ve cambios de formato ni
  documentos faltantes, que son los problemas propios de este dominio.
