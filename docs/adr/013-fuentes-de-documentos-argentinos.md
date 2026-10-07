# 013 — Fuentes de documentos de las empresas argentinas

- **Estado:** aceptada (decisiones del responsable del 2026-10-07, preguntas abiertas 17 y 18)
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

### Comunicados de resultados: CNV, SEC y sitios de inversores

- **Como hecho relevante en la CNV,** de forma consistente en los últimos 15 meses: GGAL,
  YPF, Loma Negra y Central Puerto.
- **Como 6-K en la SEC:** las 11 empresas con ADR. La descripción del 6-K no siempre dice
  qué contiene.
- **Sitios de inversores de las 9 empresas restantes** (relevados el 2026-10-07):

| Empresa | Comunicado trimestral | Dónde | Acceso |
|---|---|---|---|
| Transener | Sí (`Transener2Q26_VF.pdf`) | `transener.com.ar/home-inversores/` | PDF enlazados en la página |
| BYMA | Sí: comunicado, presentación, transcripción y audio | `byma.com.ar/relacion-con-inversores/informacion-financiera` | PDF enlazados en la página |
| TGN | Sí (`informe_de_resultados_2q_2026.pdf`) | `tgn.com.ar/inversores/informacion-financiera/` | PDF enlazados en la página |
| Banco de Valores | Sí: comunicado (en castellano e inglés), presentación y conference call | `valo.ar` | API de medios de WordPress |
| Ecogas Inversiones | Sí (`Informacion_para_inversores_2Q_2026.pdf`) | `ecogasinversiones.com.ar` (no `ecogas.com.ar`, que es la distribuidora) | API de medios de WordPress |
| Ternium Argentina | Sin verificar | `investors.ternium.com` (plataforma MZ) | Los documentos se cargan con JavaScript desde una API con autenticación. Se revisa en T3.2 con un navegador automatizado |
| Metrogas | No: el último es de noviembre de 2023 | — | — |
| Comercial del Plata | No: solo estados contables y presentación anual | — | — |
| Aluar | No: solo estados contables | — | — |

- **`robots.txt`:** los 9 sitios permiten el acceso a sus secciones de inversores.
- **Términos de uso:** ninguno prohíbe expresamente el acceso automatizado, pero tres
  restringen el uso de su contenido (pregunta abierta 19):
  - **Transener:** uso "exclusivamente privado y doméstico" y prohíbe reproducirlo sin
    consentimiento.
  - **BYMA:** prohíbe reproducirlo, salvo lo que está obligada a publicar por normativa.
  - **Aluar:** solo fines informativos y no comerciales. Aluar no publica comunicados.

## Decisión

1. **La CNV (AIF) es la fuente de estados contables de las 20 empresas.** En
   `config/universe.yaml`, cada empresa argentina tiene `cnv: {cuit, id, balance,
   cierre_ejercicio}`. El esquema exige esa ficha a toda empresa argentina.
2. **Se usa el balance consolidado,** salvo que la empresa publique solo el individual
   (TGN).
3. **Comunicados de resultados de todas las fuentes disponibles** (decisión del
   responsable: un informe completo vale el costo de mantener integraciones frágiles).
   - `comunicados` registra las fuentes de cada empresa: `cnv_hecho_relevante`, `sec_6k`,
     `sitio_inversores`.
   - `inversores` dice cómo encontrar el comunicado en el sitio de la empresa: URL, tipo de
     acceso y un patrón del nombre del archivo.
   - Para quien no publica comunicados (Metrogas, Comercial del Plata, Aluar), el informe
     usa el estado contable y la **reseña informativa** de la CNV.
4. **Monitor de integraciones (T3.7):** las fuentes frágiles avisan a `ADMIN_EMAILS` cuando
   se rompen, cambian o no publican lo esperado. Los clientes nunca reciben esos avisos.
5. **Las presentaciones de controladas y vinculadas** (filas `RELAC.: CONTROLADA ...`) se
   ignoran: son de otras sociedades.
6. **Cifras verificadas por tres caminos** (decisión del responsable): cada cifra de los
   datos estructurados de la CNV tiene que aparecer tal cual en el PDF firmado y coincidir
   con una extracción completa del PDF hecha por un LLM, que cita la página.
   - Se publica solo lo que coincide en las tres y pasa las validaciones contables.
   - Cualquier diferencia deja el documento en `revision_manual`: no genera informe hasta
     que una persona lo revise (sección de admin, T6.4).
   - El LLM revisa todas las cifras de cada documento, no solo las dudosas. Su costo se mide
     por documento.

## Alternativas consideradas

- **Sitios de inversores como fuente principal.** Habría que mantener 20 integraciones
  distintas y frágiles. Quedan como referencia humana.
- **BYMA (resúmenes del artículo 63 del reglamento de listado).** Son un resumen, no el
  estado contable completo, y no se publican en un formato uniforme.
- **Solo SEC (20-F y 6-K).** Cubre 11 de las 20 empresas, y en inglés.

## Consecuencias

- **La ingesta (T3.2) necesita un conector para la CNV, otro para la SEC y dos genéricos
  para sitios de inversores** (`enlaces_pdf` y `wordpress_media`), configurados por empresa
  en el YAML. Si un sitio cambia, se ajusta su patrón o su URL.
- La CNV no ofrece una API documentada: el conector depende del HTML y del XML de la vista
  pública. Va con tests sobre respuestas grabadas, igual que los precios (ADR 011).
- Los montos se interpretan con su `UnidadMedida`. Normalizarlos es responsabilidad del
  extractor.
