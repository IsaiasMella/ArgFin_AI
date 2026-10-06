# 001 — Herramientas de calidad y hooks de pre-commit

- **Estado:** aceptada
- **Fecha:** 2026-10-05

## Contexto

El plan técnico fija uv, Ruff, mypy (strict), pytest y pre-commit. Falta decidir cómo
se integran para que las versiones no diverjan entre la máquina local, los hooks y la CI,
y cómo se garantiza que ningún `.env` llegue al repositorio.

## Decisión

1. **uv** gestiona Python 3.12 y todas las dependencias; `uv.lock` es la única fuente de
   versiones. Las herramientas de desarrollo viven en el grupo `dev`.
2. **Hooks locales de pre-commit** (`repo: local`) que ejecutan `uv run --frozen ruff|mypy`,
   en lugar de los repos espejo (`ruff-pre-commit`, `mirrors-mypy`). Así el hook usa
   exactamente la misma versión que la CI y que el editor, y mypy ve las dependencias
   reales del proyecto (los espejos corren en un entorno aislado sin ellas, lo que da
   falsos positivos o falsos negativos en modo strict).
3. **Doble barrera contra `.env`:** `.gitignore` excluye `.env` y `.env.*` (salvo
   `.env.example`), y el hook `block-env-files` (lenguaje `fail`, sin dependencias ni red)
   rechaza cualquier commit que incluya esos archivos aunque se fuercen con `git add -f`.
   Un test de integración crea un repositorio temporal y verifica que el commit se rechaza.
4. **Ruff** con reglas de seguridad (`S`, bandit) y de fechas con zona horaria (`DTZ`),
   relevantes para un sistema con secretos y series temporales financieras.

## Alternativas consideradas

- Repos espejo de pre-commit: más habituales, pero duplican versiones y mypy no ve las
  dependencias del proyecto.
- `detect-secrets` / `gitleaks`: útiles, pero suman una dependencia más; se reevalúan cuando
  existan integraciones con credenciales reales (fase 1 en adelante).

## Consecuencias

- Los hooks requieren `uv` instalado y `uv sync` previo (`--frozen` falla si el lock está
  desactualizado, lo que es deseable).
- El hook de mypy analiza todo el proyecto en cada commit con archivos Python; si se vuelve
  lento, se puede limitar a la CI.
