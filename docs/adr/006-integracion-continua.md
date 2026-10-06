# 006 — Integración continua con GitHub Actions

- **Estado:** aceptada
- **Fecha:** 2026-10-06

## Contexto

El plan pide lint, tipos, tests y build en cada push (sección 11). El repositorio maneja
secretos en producción y una cadena de dependencias grande (LLMs, PDFs, datos financieros),
así que la CI también es superficie de ataque.

## Decisión

1. **Tres jobs en paralelo:** `Lint y tipos` (ruff + mypy strict), `Tests` (pytest con
   PostgreSQL real vía Docker) y `Build de la imagen` (Dockerfile de producción, sin
   publicar). Corren en cada PR y en cada push a `main` y `develop`.
2. **Los tests de base de datos son obligatorios en CI** (`--requiere-db`): si Docker no
   estuviera disponible fallan, en lugar de omitirse y dejar el pipeline en verde sin haber
   probado RLS.
3. **Acciones fijadas por SHA** (con el tag en un comentario): un tag se puede mover, un
   SHA no. **Dependabot** propone las actualizaciones semanales a `develop`, agrupadas.
4. **Permisos mínimos:** `contents: read` y `persist-credentials: false` en el checkout.
   La CI no usa secretos: los tests corren con valores ficticios.
5. **Misma versión de uv que en desarrollo** y `uv sync --frozen`: la CI falla si
   `uv.lock` no coincide con `pyproject.toml`.
6. **Caché** de uv y de las capas de Docker (caché de GitHub Actions).

## Alternativas consideradas

- Un solo job secuencial: más simple, pero un error de formato demoraría el resultado de
  los tests. En paralelo, cada problema aparece por separado.
- Servicio `postgres` del runner en lugar de testcontainers: obligaría a duplicar en el
  workflow el script de roles; con testcontainers los tests son iguales en local y en CI.

## Consecuencias

- Las evals no corren en cada push (cuestan dinero): se agregan como ejecución manual o
  nocturna cuando existan (T3.6).
- Publicar la imagen en un registro se resuelve con el despliegue (T8.1).
- Conviene exigir los tres checks en la protección de `develop` y `main`.
