# 010 — Selección y sincronización del universo

- **Estado:** aceptada
- **Fecha:** 2026-10-07

## Contexto

La spec (4.1) cubre 20 acciones del panel líder y 20 CEDEARs, definidos en
`config/universe.yaml`. Las preguntas abiertas (punto 8) fijaron el criterio: los más
operados del último trimestre cerrado, según la fuente más fiable, preferentemente oficial.
El universo cambia (el panel líder se rebalancea y aparecen CEDEARs nuevos), y puede haber
posiciones de usuarios que apunten a instrumentos que dejan de estar cubiertos.

## Decisión

### Selección (documentada en el YAML, bloque `seleccion`)

1. **Fuente:** BYMA Open Data, la fuente oficial del mercado. Se usan la composición del
   panel líder (`leading-equity`), la lista de CEDEARs en pesos (`cedears`) y la serie
   histórica diaria de cada especie en plazo 24 hs.
2. **Métrica:** el monto operado aproximado, que es la suma de cierre × volumen nominal de
   cada rueda. Se usa el monto porque el volumen en cantidad no sirve para comparar especies
   de precios muy distintos.
3. **Acciones:** el panel líder tiene exactamente 20 especies, así que entran todas.
4. **CEDEARs:** entran los 20 de **empresas** con mayor monto. Se excluyen los ETF y los
   fondos, porque no publican estados contables y el producto informa sobre estados
   contables (spec 4.2). Los excluidos quedan anotados en el YAML, con su monto.
5. **Datos de cada empresa:**
   - Ratios: de las listas de los emisores (Comafi y Caja de Valores) y de BYMA.
   - CIK, país y sector: de la SEC.
   - Verificación: todos los ratios se contrastaron con el ratio implícito en los precios
     (precio en EE. UU. × CCL / precio del CEDEAR), con diferencias menores al 1 %.

La selección se rehace a mano una vez por trimestre: se actualiza el YAML y se sincroniza.
Automatizarla no vale la pena para un cambio trimestral que conviene revisar con criterio.
Por ejemplo, el ranking no distingue por sí solo entre un ETF y una empresa.

### Sincronización (`python -m brujula.cli sincronizar-universo [--simular]`)

1. **El YAML es la fuente de verdad.** La base es una copia. Se valida todo el archivo con
   un esquema estricto antes de escribir nada, y se aplica en una sola transacción.
2. **Claves estables:**
   - `companies.clave`: el identificador de la empresa en el YAML, que no cambia aunque
     cambie el nombre.
   - `instruments.ticker_byma`: ya era único.
3. **Idempotente:** la segunda corrida seguida no cambia nada.
4. **Baja lógica:** lo que sale del archivo queda con `activa = false` (empresa) o
   `activo = false` (instrumento), no se borra, porque puede haber posiciones que lo
   referencian. Una posición sobre un instrumento inactivo pasa a "solo precio". Si vuelve
   al archivo, se reactiva.
5. **Vinculación:** una posición cargada como ticker libre (antes de que el ticker entrara
   al universo) se vincula a su instrumento en la misma transacción.
6. **Rol de migraciones**, como `recifrar`. Es una operación de mantenimiento, no una ruta
   de la API.
7. **`--simular`** aplica todo y deshace la transacción al final. Muestra exactamente lo
   que cambiaría.

## Alternativas consideradas

- **Elegir por cantidad de operaciones o por volumen nominal:** se descartó porque
  favorece a las especies baratas.
- **data912 como fuente del ranking:** no es oficial. Queda como respaldo de precios
  (T2.2).
- **Borrar en lugar de desactivar:** rompería las posiciones existentes (FK) o las dejaría
  apuntando a nada.
- **Sincronizar al arrancar la API:** escondería un cambio de datos dentro de un deploy.
  Un comando explícito, con simulación, es más seguro.

## Consecuencias

- Cambiar el universo es un PR que edita el YAML (revisable), más una corrida del
  comando.
- Los datos de la selección (ventana, fecha de corte, ranking) envejecen. El próximo
  rebalanceo los reemplaza.
- Solo se cargan los tickers en pesos. Una posición cargada con el ticker en dólares (por
  ejemplo `AAPLD`) queda como "solo precio".
