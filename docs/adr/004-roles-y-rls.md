# 004 — Roles de PostgreSQL, RLS y funciones SECURITY DEFINER

- **Estado:** aceptada
- **Fecha:** 2026-10-06

## Contexto

La constitución exige RLS en toda tabla con datos de usuario, y el plan, que no exista un
rol que saltee RLS. Pero el ingreso con OAuth tiene que encontrar al usuario por `sub_oauth`
y la sesión por `token_hash` **antes** de conocer su id, y el worker tiene que listar
usuarios (para el resumen semanal y para enviar el informe trimestral de una empresa).

## Decisión

1. **Tres roles**, creados por `docker/postgres-init/01-roles.sh` al inicializar el volumen:
   - superusuario de la imagen: solo crea roles y extensiones (pgvector); la app no lo usa;
   - **migraciones** (`DATABASE_URL_MIGRATIONS`): dueño del esquema, sin `SUPERUSER` ni
     `BYPASSRLS`;
   - **app** (`DATABASE_URL`, API y worker): solo `SELECT/INSERT/UPDATE/DELETE` por
     privilegios por defecto; no es dueño de ninguna tabla.
2. **RLS con `ENABLE` y `FORCE`** en cada tabla de usuario, con políticas que comparan con
   `app_current_user_id()`: lee `app.current_user_id` y devuelve NULL si no hay contexto.
   El `NULLIF(..., '')` es necesario porque, cuando la variable ya se usó en la conexión,
   fuera de la transacción vale `''` y el cast a uuid fallaría.
3. **El contexto se fija con `set_config(..., true)`** (equivalente a `SET LOCAL`), dentro de
   la transacción (`core.db.user_transaction`). Al terminarla se pierde: una conexión del
   pool nunca arrastra el usuario anterior.
4. **Operaciones previas a conocer al usuario o que enumeran usuarios** (aprobado, ver
   `specs/preguntas-abiertas.md` puntos 3 y 4): funciones `SECURITY DEFINER` acotadas, una
   por caso de uso, que:
   - pertenecen al rol de migraciones y fijan `search_path` (`SET search_path = public, pg_temp`);
   - reciben parámetros concretos y devuelven lo mínimo (ids, nunca filas completas);
   - se habilitan con `GRANT EXECUTE` solo al rol de la app (los privilegios por defecto ya
     quitan `EXECUTE` a `PUBLIC`).
   Se crean en la tarea que las necesita (T1.1: `resolver_sesion`, `upsert_usuario_oauth`;
   fase 5: enumeración de usuarios activos). Cada una tiene tests de que no filtra datos.

## Enmienda (2026-10-06, T1.1)

Con `FORCE ROW LEVEL SECURITY` el dueño de la tabla **también** queda sujeto a las
políticas, así que una función `SECURITY DEFINER` (que corre como el dueño) no veía filas.
Cada tabla de usuario lleva además una política `<tabla>_duenio ... TO CURRENT_USER USING
(true) WITH CHECK (true)`, creada por la migración y que aplica solo al rol de migraciones.
No amplía permisos reales: el dueño ya podía desactivar RLS. El rol de la app no cambia.

**Regla para toda tabla de usuario nueva:** `ENABLE` + `FORCE ROW LEVEL SECURITY`, política
`<tabla>_propio` por `user_id = app_current_user_id()`, política `<tabla>_duenio` y
`FOREIGN KEY (user_id) REFERENCES users ON DELETE CASCADE` (necesario para el borrado real
de la cuenta, T1.5).

## Alternativas consideradas

- Un rol con `BYPASSRLS` para el login y el worker: un solo error de código expondría todos
  los datos; contradice el plan.
- Políticas que permiten leer `users` sin contexto: anulan RLS para la tabla más sensible.

## Consecuencias

- Los roles y sus contraseñas se definen en `.env` (`POSTGRES_APP_*`, `POSTGRES_MIGRATOR_*`)
  y deben coincidir con las URLs. El script corre solo con un volumen vacío: cambiar una
  contraseña después requiere `ALTER ROLE`.
- Los tests de integración verifican: cero filas sin contexto, aislamiento entre usuarios,
  que el contexto no sobrevive a la transacción y que el rol de la app no puede desactivar RLS.
