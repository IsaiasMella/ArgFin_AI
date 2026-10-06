# 008 — Cifrado de campos y rotación de la clave

- **Estado:** aceptada
- **Fecha:** 2026-10-06

## Contexto

La constitución (punto 6) exige cifrar a nivel de campo los datos de portafolio: cantidad
y precio promedio (HU-02). Si se filtrara la base o un backup, esos valores no tienen que
poder leerse. La clave vive en `.env` (`FIELD_ENCRYPTION_KEY`).

## Decisión

1. **AES-256-GCM** (`cryptography`): cifrado autenticado; un valor alterado no descifra.
2. **Formato**: `versión | id de clave (4 bytes) | nonce aleatorio (12) | cifrado + tag`.
3. **Datos asociados = columna** (`holdings.cantidad`): un valor copiado a otra columna o
   tabla no descifra.
4. **Tipo de columna `EncryptedDecimal`** (SQLAlchemy): el código trabaja con `Decimal` y
   la base solo ve `bytea`. El cifrador se configura al arrancar cada proceso.
5. **Rotación por id de clave**: `FIELD_ENCRYPTION_KEY` cifra; las claves en
   `FIELD_ENCRYPTION_KEYS_PREVIOUS` solo descifran.

Consecuencia aceptada: los valores cifrados **no se pueden filtrar, ordenar ni sumar en
SQL**. Los cálculos de portafolio se hacen en la aplicación, por usuario (son 5 a 20
posiciones).

## Procedimiento de rotación

1. Generar la clave nueva:
   `uv run python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`
2. En `.env`: la nueva en `FIELD_ENCRYPTION_KEY` y la actual en
   `FIELD_ENCRYPTION_KEYS_PREVIOUS`. Reiniciar API y worker: leen ambas y escriben con la nueva.
3. Re-cifrar lo existente con el comando `recifrar` (T1.3): recorre las filas cuyo id de
   clave no es el actual y las vuelve a cifrar en lotes, con el rol de migraciones.
4. Verificar que el comando informe 0 valores pendientes.
5. Quitar la clave vieja de `FIELD_ENCRYPTION_KEYS_PREVIOUS` y reiniciar.
6. **Guardar la clave vieja fuera del servidor mientras existan backups cifrados con ella**
   (según la retención de backups). Sin esa clave, esos backups no se pueden restaurar.

Cuándo rotar: ante sospecha de filtración de la clave o del `.env`, al salir alguien con
acceso al servidor y, como higiene, una vez por año.

## Alternativas consideradas

- `pgcrypto` en la base: la clave viajaría en cada consulta y quedaría en logs de
  PostgreSQL; con el cifrado en la aplicación, la base nunca la ve.
- Cifrado de disco o de volumen: protege si se roba el disco, no si se filtra un dump.
