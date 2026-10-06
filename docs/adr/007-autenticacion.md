# 007 — Autenticación con Google: flujo, sesiones, CSRF y límites

- **Estado:** aceptada
- **Fecha:** 2026-10-06

## Contexto

HU-01 y el plan (§6.3–6.6) piden OAuth 2.0 / OIDC con Google, Authorization Code + PKCE,
validación de `state`, `nonce` y firma del ID token, sesiones del lado del servidor con
cookie `HttpOnly`/`Secure`/`SameSite=Lax`, CSRF con `Origin` + doble envío y rate limiting.

## Decisión

1. **Flujo propio sobre `joserfc`** (el sucesor de `authlib.jose`, del mismo autor) en lugar
   del cliente de Authlib para Starlette: ese cliente guarda `state` y el verificador PKCE en
   una cookie firmada pero no cifrada. Acá viven en `oauth_transactions` (en la base), duran
   10 minutos y se **consumen al usarse** (un `state` no se puede reutilizar).
2. **Doble atadura del `state`**: debe coincidir con la cookie `brujula_oauth_state` del mismo
   navegador (evita el CSRF de ingreso) y existir en la base sin vencer.
3. **ID token validado completo**: firma RS256 con el JWKS de Google (si aparece un `kid`
   desconocido se refresca una vez, por la rotación de claves), `iss`, `aud`, `exp` (60 s de
   tolerancia), `nonce` del flujo y `email_verified`. Endpoints por descubrimiento OIDC
   (`GOOGLE_DISCOVERY_URL`): ninguna URL de Google queda en el código.
4. **Sesión del lado del servidor**: token aleatorio de 256 bits en la cookie; en la base,
   solo su SHA-256. Dura `SESSION_TTL_HOURS`. El logout borra la fila (invalidación real).
5. **Login con RLS**: `upsert_usuario_oauth` y `resolver_sesion` son `SECURITY DEFINER`
   (ADR 004). El rol `admin` se recalcula en cada ingreso desde `ADMIN_EMAILS`; el plan se
   asigna solo al crear el usuario (`fundador` mientras `FOUNDER_PLAN_OPEN=true`).
6. **CSRF**: en métodos que modifican estado, `Origin` (o `Referer`) debe ser el frontend o
   la API, y `X-CSRF-Token` debe ser HMAC(`CSRF_SECRET`, token de sesión). El frontend lo lee
   de la cookie `brujula_csrf` (no HttpOnly). Una cookie plantada sin el secreto no sirve.
7. **CORS** solo para `WEB_BASE_URL`, con credenciales.
8. **Rate limiting** en memoria por IP (`RATE_LIMIT_AUTH_PER_MINUTE`) en `/auth/google/*`.
   Válido mientras la API corra en un proceso; con réplicas se mueve a PostgreSQL.
9. **Logs**: ingresos rechazados con motivo e IP seudonimizada (HMAC con clave derivada de
   `SESSION_SECRET`); nunca tokens, emails ni IPs en claro.

## Alternativas consideradas

- JWT en cookie sin estado: no se puede invalidar en el servidor (HU-01 lo exige).
- Authlib completo: ver punto 1; además depende de `httpx` y el proyecto usa `httpx2`.

## Consecuencias

- `Secure` siempre: en desarrollo funciona porque los navegadores tratan `localhost` como
  contexto seguro (Safari puede no hacerlo).
- Las sesiones vencidas quedan en la tabla hasta que se agregue una tarea de limpieza (no
  afectan: `resolver_sesion` filtra por vencimiento).
