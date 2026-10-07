# 012 — Tarea diaria de precios y tipo de cambio CCL

- **Estado:** aceptada
- **Fecha:** 2026-10-07

## Contexto

El plan (7.1) pide guardar cada día el cierre de los instrumentos del universo, validado
contra una segunda fuente, y el tipo de cambio CCL. Los informes muestran valores en pesos
y en dólares CCL (spec, HU-04). Criterio de T2.3: la tarea es idempotente y un dato
marcado no aparece en informes.

## Decisión

### Validación cruzada (`features/prices/reconcile.py`, funciones puras)

| Situación de la rueda | Qué se guarda | Marcado |
|---|---|---|
| Las dos fuentes la tienen | El cierre de BYMA (principal), el de data912 como `cierre_respaldo` y la divergencia | Sí, si la divergencia es **mayor** que `PRICE_DIVERGENCE_THRESHOLD_PCT` |
| Solo una fuente la tiene | El cierre de esa fuente, con respaldo y divergencia nulos | No. Queda en el log (`precio_solo_respaldo`) |
| Ninguna la tiene | Nada: feriado, fin de semana o especie sin operaciones | — |

- **Divergencia:** `|principal − respaldo| / principal × 100`, redondeada a 4 decimales.
- **Ante una divergencia no se elige "la correcta":** se guarda el dato oficial y se marca.
  Lo resuelve una persona (el panel de revisión es de la fase 6).

### CCL (`config/fx.yaml`)

- **Fórmula:** el CCL implícito es el cierre del **AL30** en pesos dividido por el del
  **AL30C** en dólares cable, de la misma rueda y la misma fuente. Se calcula en cada
  fuente y se valida igual que un precio.
- **Por qué el AL30:** es el par más líquido. En el tercer trimestre de 2026 operó unas 11
  veces el monto del GD30 en pesos y unas 49 veces en cable.
- **Cambiar de bono** es editar el YAML. No es un número de negocio, sino la metodología
  versionada.
- **No se guardan el MEP ni el oficial**, aunque el plan los listaba: ningún requisito del
  MVP los usa. Sumarlos es agregar columnas y un par en el YAML.

### Idempotencia y ventana

- **Clave natural:** `(instrument_id, fecha)` en `prices_daily` y `fecha` en `fx_daily`.
- **Upsert que solo escribe si algo cambió** (`IS DISTINCT FROM`). Una corrida repetida no
  toca ninguna fila, ni siquiera `actualizado_en`.
- **Ventana:** cada corrida revisa los **últimos 7 días**. Así recupera días perdidos (el
  worker estuvo caído) y reemplaza datos de respaldo por los oficiales cuando BYMA publica
  tarde.
- **Un dato oficial ya guardado nunca se reemplaza por uno que solo tiene el respaldo.** Si
  BYMA falla en una corrida, los días que ya tenía se conservan. Esto se vio en una prueba
  real: una falla transitoria de BYMA en 2 tickers habría pisado 8 cierres validados.

### Ajustes retroactivos (eventos corporativos)

BYMA ajusta su serie hacia atrás ante splits y otros eventos (ADR 011). Si un ajuste cae
dentro de la ventana, la corrida reescribe esos días con valores ajustados. data912 sigue
con los precios tal como se operaron, así que la divergencia los marca. Pero los cierres
guardados **antes** de la ventana quedarían sin ajustar y sin marcar: una variación mensual
mostraría una caída falsa (por ejemplo, −90 % en un split 10:1).

**Regla:** si la principal reescribe un cierre oficial ya guardado más allá del umbral, se
marcan todos los cierres anteriores a la ventana de ese instrumento (`ajuste_retroactivo`
en el log). Así ningún informe compara precios de antes y de después del evento. Para
volver a publicarlos hay que revisarlos (fase 6). El tratamiento completo de los eventos
corporativos está en el `ROADMAP.md` (pregunta abierta 16).

Al cargar la historia del tercer trimestre de 2026 con datos reales, los cierres previos a
esos eventos (YPFD hasta el 31/07 y METR hasta mediados de julio) quedaron marcados por la
divergencia. Es el comportamiento buscado.

### Lectura para informes (`features/prices/service.py`)

`publishable_closes` y `publishable_ccl` son la única puerta para leer precios y **excluyen
los datos marcados**. Si falta un dato, el informe lo indica; nunca lo reemplaza por otro
día.

### Ejecución

- **Tarea `precios:actualizar_diario`** de Procrastinate, agendada con `CRON_PRICES_DAILY`.
  - Procrastinate evalúa el cron en UTC. El proyecto lo ancla a `APP_TIMEZONE`, como dice
    `.env.example` (`core/queue.py`, con un test).
  - Si ninguna fuente devuelve datos y hubo errores, falla y se reintenta a los 15
    minutos, hasta 3 intentos.
- **Comando para cargar historia:**
  `python -m brujula.cli actualizar-precios --desde AAAA-MM-DD --hasta AAAA-MM-DD`. Usa el
  rol de la app, igual que el worker.
- **Fábricas de blueprints:** Procrastinate modifica el blueprint al agregarlo a una app.
  Por eso cada feature registra una función que arma el suyo.

## Alternativas consideradas

- **Promediar las dos fuentes:** mezclaría un dato oficial con uno no oficial, y el
  resultado no existiría en ninguna fuente.
- **Marcar todo lo que no tenga validación cruzada:** si BYMA se cae, ningún informe
  tendría precios. El plan pide usar el respaldo y registrarlo, y eso se hace.
- **CCL de un proveedor externo (por ejemplo, el CCL "de mercado" de una web):** sería otra
  fuente no oficial y opaca. Con los bonos, el cálculo se puede reproducir con datos de
  BYMA.
- **Cron convertido a mano a UTC:** se rompe con zonas que tienen horario de verano y
  obliga a quien opera a pensar en UTC.

## Consecuencias

- **data912 no tiene historia de 9 de los 40 tickers del universo:** ECOG, MRNA, NBIS, NU,
  ORCL, PLTR, SPCX, SNDK y VIST (verificado el 2026-10-07). Esos cierres se guardan solo
  con BYMA, sin validación cruzada, y la corrida los informa en `errores`. Si hace falta
  validarlos, la opción es una tercera fuente detrás de la misma interfaz.
- Para revisar un dato marcado hace falta una herramienta de admin (fase 6). Hasta
  entonces, se revisa con SQL.
- Las posiciones fuera del universo no tienen precio todavía (pregunta abierta 15).
- Después de un evento corporativo, la historia previa del instrumento queda fuera de los
  informes hasta que alguien la revise (pregunta abierta 16).
