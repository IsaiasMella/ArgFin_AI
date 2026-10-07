# 011 — Proveedores de precios

- **Estado:** aceptada
- **Fecha:** 2026-10-07

## Contexto

El plan técnico define BYMA Open Data como fuente principal y data912 como respaldo, detrás
de una interfaz `PriceProvider`, para poder validar los precios cruzando las dos fuentes
(T2.3). Ninguna tiene una API documentada ni un contrato de servicio: pueden cambiar sin
aviso (riesgo listado en el plan, §10).

## Decisión

1. **Interfaz única** (`features/prices/providers.py`):
   `daily_bars(símbolos, desde, hasta) -> FetchResult`.
   - Devuelve las ruedas con fecha, cierre y volumen, y un error por especie para las que no
     pudo traer.
   - Una especie que falla no corta a las demás.
   - Un proveedor nunca inventa una rueda: si la fuente no la tiene, no aparece.
2. **BYMA Open Data** (oficial). Usa la serie histórica diaria
   (`chart/historical-series/history`) en plazo 24 hs, que es el plazo estándar desde
   2024. Es un pedido por especie con el rango exacto.
   - Las ruedas vienen marcadas con la medianoche de Buenos Aires (03:00 UTC). La fecha
     UTC de la marca es la fecha de la rueda, así que no depende de `APP_TIMEZONE`.
   - Un ticker sin datos responde `no_data`. Es una lista vacía, no un error.
3. **data912** (no oficial). Usa la historia diaria por tipo (`historical/stocks|cedears|
   bonds/{ticker}`). La fuente entrega la historia completa y el proveedor filtra el rango.
   Un ticker desconocido responde con un JSON de error, que se informa como
   `ticker_desconocido`.
4. **Ajustes por eventos corporativos.**
   - **BYMA ajusta su serie histórica hacia atrás** cuando hay un evento corporativo.
     Ejemplos verificados el 2026-10-07: YPFD tuvo un split 10:1 a fines de julio de 2026 y
     BYMA divide por 10 los cierres previos; METR tuvo un ajuste de alrededor del 9,5 % a
     mediados de julio.
   - **data912 da los precios tal como se operaron.**
   - En las ruedas recientes y sin eventos, las dos coinciden rueda por rueda (hay un test
     que lo verifica con las respuestas grabadas).
   - Alrededor de un evento difieren a propósito, y la tarea diaria lo trata (ADR 012).
5. **Decimales exactos.** El JSON se lee con `Decimal` desde el texto, sin pasar por
   `float`. Se rechazan los cierres nulos, no numéricos o menores o iguales a cero.
6. **Robustez:**
   - Hasta 3 intentos ante errores de red, 429 y 5xx, con espera creciente.
   - Un 4xx no se reintenta.
   - Como mucho 4 pedidos simultáneos por proveedor, para no saturar fuentes gratuitas.
   - El timeout es el del cliente HTTP compartido.
7. **Configuración:** las URLs base van en `.env` (`BYMA_OPEN_DATA_BASE_URL`,
   `DATA912_BASE_URL`).
8. **Tests sin red:** `tests/fixtures/prices` guarda respuestas reales grabadas el
   2026-10-07, recortadas a las ruedas del 24/09 al 06/10. `tests/support/markets.py` las
   sirve con un transporte simulado y permite forzar fallas.

## Alternativas consideradas

- **PyOBD** (cliente de BYMA Open Data, que el plan mencionaba). Usa la misma API, pero
  suma una dependencia síncrona (requests) y otra capa entre el código y la fuente, y acá
  alcanza con un endpoint. Se habla con la API directamente, con el cliente asíncrono que
  ya usa el proyecto.
- **Endpoints "en vivo" de data912** (una sola llamada para todo el panel). Son más
  baratos, pero no traen la fecha de la rueda: después de un feriado, el dato de ayer se
  guardaría como el de hoy. Se descartaron para guardar cierres.
- **Precios de Yahoo Finance u otros agregadores internacionales.** No cubren bien las
  especies locales y sus términos de uso son más restrictivos.

## Consecuencias

- Agregar una fuente nueva es implementar `daily_bars`. El resto del sistema no cambia.
- Si una fuente cambia su formato, el proveedor lo informa como error de la especie
  (`formato_inesperado`) y no guarda datos dudosos. La tarea diaria lo registra (T2.3).
- data912 descarga la historia completa de cada especie. Para unas 40 especies al día es
  aceptable. Si el universo crece mucho, conviene pedirle un endpoint por rango.
