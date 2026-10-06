# Brújula — API

Monolito modular en Python (API FastAPI + worker). Ver `AGENTS.md` y `specs/`.

## Desarrollo

```bash
uv sync
uv run pre-commit install
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```
