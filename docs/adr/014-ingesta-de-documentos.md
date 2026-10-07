# 014 — Ingesta de documentos

- **Estado:** aceptada
- **Fecha:** 2026-10-07

## Contexto

El ADR 013 definió las fuentes de cada empresa argentina: la CNV, la SEC y los sitios de
inversores. T3.2 pide descubrir, descargar, calcular el hash y guardar esos documentos, de
forma idempotente (plan, §7) y sin que la falla de una fuente frene a las demás.

## Decisión

### Qué se guarda

| Fuente | Qué | Tipo de documento |
|---|---|---|
| CNV, estados contables | Solo los propios, del balance elegido (`cnv.balance`) y desde `desde` | Datos estructurados en `cnv_statements` y los PDF configurados (`config/documents.yaml`): `estado_contable`, `resena_informativa`, `memoria_anual` |
| CNV, hechos relevantes | Los que son comunicados de resultados (patrón sobre la descripción, excluyendo anuncios de fecha, ofertas y asambleas) | `comunicado_resultados` |
| SEC, 6-K | Los archivos `.htm`/`.pdf` cuyo texto inicial coincide con un patrón de comunicado de resultados | `comunicado_resultados` |
| Sitio de inversores | Los archivos que coinciden con el patrón de la empresa, de períodos recientes | `comunicado_resultados` |

- **Archivos de relleno:** los que las empresas suben cuando un adjunto no aplica ("NO
  CORRESPONDE ARCHIVO OBLIGATORIO") se ignoran.
- **Informes del auditor y de la comisión fiscalizadora:** no se guardan, porque no aportan
  cifras.

### Idempotencia

- **Identidad de cada documento:** `(fuente, clave_externa)`, con unicidad en la base:
  - CNV: `cnv:<presentación>:<guid>`
  - SEC: `sec:<accession>:<archivo>`
  - Sitios: `web:<url>`
- **Ítems revisados que no interesaban:** se recuerdan en `document_checks` (un 6-K que no
  es un comunicado, por ejemplo). Así no se vuelven a descargar en cada corrida. Sin esto,
  YPF o Central Puerto, con casi 20 6-K por trimestre, se descargarían una y otra vez.
- **Datos estructurados primero:** un estado contable se registra en `cnv_statements`
  después de descargar sus adjuntos y en la misma transacción. Si algo falla en el medio,
  la próxima corrida lo reintenta completo.

### Almacenamiento

- **`DocumentStorage`** (hoy `DiskStorage` bajo `DOCUMENT_STORAGE_DIR`) guarda cada archivo
  por su SHA-256 (`ab/abcd….pdf`). El mismo contenido se guarda una sola vez, aunque lo
  publiquen dos fuentes.
- **Escritura atómica:** archivo temporal y después renombre.
- **Rutas acotadas:** no se puede leer fuera de la carpeta.
- **En Docker,** un volumen `documents` en `/app/data/documents`, que entra en los backups
  (T8.2).

### Casos reales que se contemplan

Aparecieron cargando datos reales el 2026-10-07:

- **BYMA presenta dos veces cada balance:** primero sin firmar y una semana después firmado
  y legalizado. Se guardan las dos presentaciones. Quien procese un período usa la de
  mayor id (la última).
- **Ecogas sube una "nota de presentación" como adjunto del estado contable.** El balance
  que queda vinculado a `cnv_statements.document_id` es el PDF de mayor tamaño.
- **Algunos sitios de inversores listan comunicados desde 2022.** Solo se toman los de
  períodos que cerraron hasta un trimestre antes de `desde`.
- **GGAL publica el mismo comunicado en la CNV (PDF) y en la SEC (HTML).** Se guardan los
  dos: son fuentes distintas y el contenido no es idéntico.

### Robustez y cortesía

- **Pedidos:** hasta 3 intentos ante red caída, 429 y 5xx (`core/http.fetch`), y de a uno
  por fuente.
- **SEC:** se identifica con `SEC_USER_AGENT`, como exige.
- **Sitios de inversores:** se identifican como Brújula en el User-Agent.
- **Errores:** cada paso (CNV, hechos relevantes, SEC, sitio) captura sus propios errores
  (`FetchError`, cambios de formato de la CNV) y los deja en el informe de la corrida. Los
  demás pasos siguen. Esos errores alimentan el monitor de integraciones (T3.7).

### Ejecución

- **Tarea `documentos:buscar`** con `CRON_FILINGS_CHECK`, en `APP_TIMEZONE`.
- **Comando** `python -m brujula.cli ingestar-documentos [--empresa CLAVE] [--desde
  AAAA-MM-DD]`, por ejemplo para cargar historia.
- **URLs base en `.env`:** `CNV_BASE_URL`, `CNV_AIF_BASE_URL`, `CNV_BLOB_BASE_URL`,
  `SEC_DATA_BASE_URL` y `SEC_ARCHIVES_BASE_URL`. Los patrones y la fecha `desde` están en
  `config/documents.yaml`.

### Términos de uso (pregunta abierta 19)

Mientras el responsable decide, se aplica la recomendación: se integran los sitios de
inversores, porque el sistema guarda los documentos para trazabilidad interna y no los
redistribuye. Los informes publican cifras (hechos) y texto propio, con la fuente citada.
La revisión legal previa al cobro (T9.4) lo confirma.

## Alternativas consideradas

- **Clasificar los 6-K por nombre de archivo:** no funciona. Pampa usa `pampr2q26_6k.htm`,
  Telecom `tm2622474d2_6k.htm` y Edenor cinco 6-K el mismo día. Por eso se lee el texto.
- **Guardar todos los 6-K:** casi 20 por trimestre en algunas empresas, la mayoría
  irrelevantes (avisos de asambleas, de recompras, de calificaciones).
- **Navegador automatizado para todos los sitios:** solo hace falta para Ternium Argentina
  (plataforma MZ, pendiente). Los demás se leen con HTML o con la API de WordPress, que es
  más simple y estable.

## Consecuencias

- Para sumar un sitio de inversores basta con configurarlo en `universe.yaml` si usa uno
  de los dos lectores genéricos. Si no, hace falta un lector nuevo.
- `document_checks` evita volver a revisar un ítem. Si se ajusta un patrón para reconocer
  6-K que antes no se reconocían, hay que borrar sus filas para que se revisen otra vez.
