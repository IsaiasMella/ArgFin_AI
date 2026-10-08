# Brújula — API

Monolito modular en Python (API FastAPI + worker). Ver `AGENTS.md` y `specs/`.

## Desarrollo

```bash
uv sync
uv run pre-commit install
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

## Levantar todo con Docker

```bash
cp .env.example .env   # y completá los valores (DATABASE_URL con host "postgres")
docker compose -f docker/compose.yml --env-file .env up -d --build
curl http://localhost/health   # con CADDY_SITE_ADDRESS=http://localhost
curl http://localhost/ready    # base de datos y cola disponibles
```

Servicios: `migrate` (aplica las migraciones y termina), `api` (FastAPI), `worker`
(Procrastinate), `postgres` (con pgvector) y `caddy`. Solo Caddy publica
puertos (80/443); el resto queda en la red interna de Docker.

## Operación

```bash
# Re-cifrar los datos de portafolio después de rotar la clave (docs/adr/008)
docker compose -f docker/compose.yml --env-file .env run --rm api python -m brujula.cli recifrar

# Cargar o actualizar el universo desde config/universe.yaml (docs/adr/010).
# Primero con --simular para ver los cambios; después sin él para aplicarlos.
docker compose -f docker/compose.yml --env-file .env run --rm api python -m brujula.cli sincronizar-universo --simular

# Cargar precios y CCL de un rango (el worker lo hace solo cada día, docs/adr/012)
docker compose -f docker/compose.yml --env-file .env run --rm api python -m brujula.cli actualizar-precios --desde 2026-07-01

# Buscar y descargar documentos de las empresas argentinas (el worker lo hace solo, docs/adr/014)
docker compose -f docker/compose.yml --env-file .env run --rm api python -m brujula.cli ingestar-documentos --empresa GGAL

# Cifras XBRL de la SEC de los CEDEARs (el worker lo hace solo, docs/adr/015)
docker compose -f docker/compose.yml --env-file .env run --rm api python -m brujula.cli actualizar-sec

# Verificación triple de los estados contables argentinos: CNV, PDF y LLM (docs/adr/017).
# Necesita LLM_EXTRACTION_MODEL y la clave del proveedor; --modelo prueba otro modelo.
docker compose -f docker/compose.yml --env-file .env run --rm api python -m brujula.cli verificar-estados --empresa GGAL --limite 1
```
