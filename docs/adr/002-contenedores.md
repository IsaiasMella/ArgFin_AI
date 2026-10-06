# 002 — Imagen y orquestación con Docker Compose

- **Estado:** aceptada
- **Fecha:** 2026-10-06

## Contexto

El plan fija un VPS con Docker Compose (`api`, `worker`, `postgres`, `caddy`) y una sola
imagen para API y worker. Hay que decidir cómo construir la imagen y cómo limitar la
superficie expuesta y la circulación de secretos.

## Decisión

1. **Dockerfile multi-stage con uv:** la etapa de build instala dependencias desde
   `uv.lock` (`--frozen`, capa cacheada aparte del código) y el paquete sin modo editable;
   el runtime copia solo el entorno virtual. Sin uv, sin compiladores, sin código fuente
   suelto, con un usuario sin privilegios.
2. **Solo Caddy publica puertos.** API, worker y PostgreSQL quedan en la red interna.
   Uvicorn confía en los encabezados de proxy porque el único que llega a la API es Caddy.
3. **Secretos por servicio:** `api` y `worker` reciben el `.env`; `postgres` y `caddy`
   reciben solo sus variables por interpolación (`${VAR:?mensaje}`, que falla si faltan).
4. **`.dockerignore`** excluye `.env*`, `.git` y todo lo que no es código de producción.
5. **Imágenes base configurables** por `ARG` (`PYTHON_IMAGE`, `UV_IMAGE`), con la versión de
   uv igual a la de desarrollo.

## Alternativas consideradas

- Publicar el puerto de la API además de Caddy: más cómodo para depurar, pero expone un
  servicio sin TLS ni encabezados de seguridad.
- Pasar el `.env` completo a todos los servicios: más simple, pero entrega secretos de la
  app a contenedores que no los necesitan.

## Consecuencias

- Los comandos de compose necesitan `-f docker/compose.yml --env-file .env` desde la raíz.
- Para producción conviene fijar las imágenes base por digest (se hace en T8.1).
