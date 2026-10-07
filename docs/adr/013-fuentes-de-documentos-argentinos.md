# 013 — Fuentes de documentos de las empresas argentinas

- **Estado:** aceptada (la recomendación de la sección "Impacto en la fase 3" espera el OK del
  responsable, pregunta abierta 18)
- **Fecha:** 2026-10-07

## Contexto

T3.1 pide documentar, para cada empresa argentina del universo, dónde y en qué formato
publica sus estados contables y comunicados. El plan (7.2) mencionaba los sitios de
relación con inversores y la CNV. Los sitios de inversores son heterogéneos: cada uno tiene
su estructura, cambia sin aviso y no siempre tiene todo.

## Relevamiento (2026-10-07, las 20 empresas del panel líder)

### Estados contables: la CNV los tiene todos

La **Autopista de Información Financiera (AIF)** de la CNV es la fuente oficial y uniforme.
Las 20 empresas publican ahí sus estados trimestrales y anuales, consolidados e
individuales. Hay dos excepciones: TGN publica solo el individual y Banco de Valores solo el
consolidado. Cada presentación trae tres cosas:

1. **Metadatos:** período (`PeriodoBalance`: 3 trimestral, 1 anual), fecha de cierre, tipo
   de balance, moneda, **unidad** (varía: miles de $, millones de $, y $ en el caso de Aluar)
   y norma contable (NIIF en las 20).
2. **Datos estructurados:** el plan de cuentas de la CNV (código, rubro y monto), con entre
   71 y 146 cuentas según la plantilla (bancos, industria). Incluye total del activo,
   pasivo y patrimonio, resultado del período, flujos de efectivo, EBITDA, ganancia por
   acción e indicadores.
   - **Verificado:** en GGAL al 30/06/2026, el total del activo, el pasivo, el patrimonio y el
     resultado de la estructura coinciden con el PDF firmado (página 3).
3. **PDF adjuntos:** el estado contable, el informe del auditor, el de la comisión
   fiscalizadora, la reseña informativa y la memoria (anual). Cada uno trae su nombre y un
   identificador (`guid`).

**Acceso:** sin autenticación, verificado. Los identificadores de cada empresa (CUIT e id)
están en `config/universe.yaml`; las URLs base van a `.env` cuando se implemente la ingesta
(T3.2).

| Paso | Pedido |
|---|---|
| Buscar empresa → CUIT e id | `GET https://www.cnv.gov.ar/SitioWeb/Empresas/AutoComplete?term=<nombre>` (JSON) |
| Listar estados contables | `GET https://www.cnv.gov.ar/SitioWeb/Empresas/Empresa/<cuit>?formType=INFOFI&fdesde=<d/m/aaaa>` (HTML; cada fila tiene su link a la presentación) |
| Datos y adjuntos de una presentación | `GET https://aif2.cnv.gov.ar/presentations/publicview/<uuid>` (HTML con un XML embebido en `var presentation = '...'`) |
| Descargar un PDF | `GET https://aif2.cnv.gov.ar/api/ValetKeyProvider/GetPublicValetKey/<guid>?operation=DownloadBlob` → `POST https://blob.cnv.gov.ar/BlobWebService.svc/DownloadBlob/<guid>` con `ValetKey=<clave>` |

**Calidad de los datos estructurados:** los carga cada empresa en un formulario y no
siempre vienen limpios.
- **Formatos de monto distintos:** `329308168.00`, `25756262378`, `43.346.287` (con puntos
  de miles), y `-` cuando la cuenta está vacía.
- **Cuentas que dependen de la plantilla:** un banco no tiene "ingresos por ventas".
- **Valores que no se pueden usar tal cual:** BBVA informa un EBITDA de `0.00`, y Loma Negra
  deja `-` en el patrimonio del ejercicio anterior.

Por eso **se valida igual que cualquier extracción**: identidades contables, signos y
comparación con el PDF.

### Comunicados de resultados: no hay una fuente uniforme

- **Como hecho relevante en la CNV,** de forma consistente en los últimos 15 meses: GGAL,
  YPF, Loma Negra y Central Puerto.
- **Como 6-K en la SEC:** las 11 empresas con ADR. La descripción del 6-K no siempre dice
  qué contiene.
- **Sin fuente uniforme:** Transener, BYMA, Metrogas, Ternium Argentina, TGN, Banco de
  Valores, Ecogas, Comercial del Plata y Aluar. Solo sus sitios de inversores, que no
  verifiqué.

## Decisión

1. **La CNV (AIF) es la fuente de estados contables de las 20 empresas.** En
   `config/universe.yaml`, cada empresa argentina tiene `cnv: {cuit, id, balance,
   cierre_ejercicio}`. El esquema exige esa ficha a toda empresa argentina.
2. **Se usa el balance consolidado,** salvo que la empresa publique solo el individual
   (TGN).
3. **`comunicados` registra dónde publica cada empresa sus comunicados de resultados**
   (`cnv_hecho_relevante`, `sec_6k`). Cómo tratar a las que no tienen una fuente uniforme es
   la pregunta abierta 17.
4. **Los sitios de inversores no se usan como fuente automática:** son heterogéneos y no
   aportan nada que la CNV no tenga para los estados contables.
5. **Las presentaciones de controladas y vinculadas** (filas `RELAC.: CONTROLADA ...`) se
   ignoran: son de otras sociedades.

## Impacto en la fase 3 (recomendación, pregunta abierta 18)

El plan prevé extraer las cifras de los PDF con un modelo multimodal (T3.5). Con los datos
estructurados de la CNV, las cifras principales se pueden leer **con código, sin LLM**, como
se hace con el XBRL de la SEC (T3.3). Ventajas:

- **Constitución, punto 2:** los números los pone el código.
- **Costo:** sin LLM para las cifras principales.
- **Exactitud:** los valores son los que la empresa declaró a la CNV.

**Recomendación:**
- Fuente principal de las métricas del plan de cuentas de la CNV: los datos estructurados,
  validados.
- El PDF con extracción por LLM (T3.5), solo para lo que el plan de cuentas no tiene (por
  ejemplo, métricas por sector o segmentos) y para contrastar cuando la estructura viene
  incompleta o inconsistente.
- La eval de extracción (T3.6) mide ambos caminos.

## Alternativas consideradas

- **Sitios de inversores como fuente principal.** Habría que mantener 20 integraciones
  distintas y frágiles. Quedan como referencia humana.
- **BYMA (resúmenes del artículo 63 del reglamento de listado).** Son un resumen, no el
  estado contable completo, y no se publican en un formato uniforme.
- **Solo SEC (20-F y 6-K).** Cubre 11 de las 20 empresas, y en inglés.

## Consecuencias

- La ingesta (T3.2) tiene un solo conector para las empresas argentinas. Si la CNV cambia
  su sitio, se arregla en un lugar.
- La CNV no ofrece una API documentada: el conector depende del HTML y del XML de la vista
  pública. Va con tests sobre respuestas grabadas, igual que los precios (ADR 011).
- Los montos se interpretan con su `UnidadMedida`. Normalizarlos es responsabilidad del
  extractor.
