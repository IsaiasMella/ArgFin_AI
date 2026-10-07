# 009 — Borrado real de la cuenta

- **Estado:** aceptada
- **Fecha:** 2026-10-06

## Contexto

HU-08 y el plan (§6.8) piden que el usuario pueda borrar su cuenta y sus datos con un
borrado real, no lógico. Aplica la Ley 25.326 de protección de datos personales (con
revisión legal pendiente, T9.4).

## Decisión

1. **`DELETE /me`** borra la fila de `users`. Todas las tablas de usuario tienen
   `FOREIGN KEY (user_id) REFERENCES users ON DELETE CASCADE`, así que sesiones, posiciones
   y todo lo que se agregue después caen en la misma transacción.
2. **Corre con el contexto RLS del propio usuario**: la política de `users` solo permite
   borrar la fila propia. No hace falta ninguna función privilegiada.
3. **Confirmación explícita**: el pedido debe traer el email de la cuenta
   (`confirmacion_email`), además de la sesión y el token CSRF. Un clic accidental o un
   pedido forjado no alcanza.
4. **Guardianes**: un test recorre el catálogo de PostgreSQL y falla si alguna tabla con
   columna `user_id` no tiene RLS forzado o no borra en cascada. Una tabla nueva que se
   olvide de las reglas rompe la CI.
5. **Después del borrado**: se borran las cookies y se registra el evento con el id
   seudonimizado. Si la persona vuelve a ingresar con Google, es una cuenta nueva.

## Qué no se borra en el momento

- **Backups**: los datos siguen en los backups hasta que vence su retención (T8.2). Las
  posiciones ahí están cifradas. El plazo se informa en la política de privacidad.
- **Logs**: solo tienen ids seudonimizados (HMAC), nunca email ni datos de portafolio.
- **LLMs y Langfuse**: nunca reciben datos de usuarios (plan §8).
- **Métricas agregadas futuras** (fase 6): deben guardarse sin datos personales o con
  `user_id` en cascada, según esta misma regla.
