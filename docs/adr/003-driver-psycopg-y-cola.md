# 003 — psycopg 3 como único driver y esquema de la cola en Alembic

- **Estado:** aceptada (reemplaza "asyncpg" en la tabla de stack del plan técnico)
- **Fecha:** 2026-10-06

## Contexto

El plan elegía SQLAlchemy async con asyncpg y Procrastinate como cola sobre PostgreSQL.
Procrastinate 3 solo trae conectores para psycopg (2 y 3): no soporta asyncpg. Además, la
cola necesita su propio esquema (tablas, funciones, triggers) y la base tiene que poder
reconstruirse desde cero con un solo comando.

## Decisión

1. **psycopg 3 para todo:** SQLAlchemy async (`postgresql+psycopg://`), Alembic (el mismo
   driver en modo sincrónico) y Procrastinate (`PsycopgConnector`). La configuración rechaza
   URLs con otro driver.
2. **El esquema de Procrastinate se aplica con Alembic** (migración 0002), desde una copia
   congelada en `migrations/sql/procrastinate-<versión>.sql`. Así la migración es inmutable
   aunque se actualice la librería. Si una versión nueva trae cambios de esquema, se agrega
   otra migración con sus archivos de `procrastinate/sql/migrations`.
3. Las tareas se declaran en `Blueprint`s por feature y se registran en `core/queue.py`.
4. `/ready` considera la cola disponible si existe su esquema (vive en la misma base).

## Alternativas consideradas

- asyncpg para la app y psycopg solo para la cola: dos drivers, dos pools y dos formatos
  de URL para el mismo PostgreSQL, sin beneficio medible a esta escala.
- `procrastinate schema --apply` fuera de Alembic: dos mecanismos de migración y una base
  que no se reconstruye con `alembic upgrade head`.

## Consecuencias

- En Windows, psycopg asíncrono requiere `SelectorEventLoop` (lo usan el worker y los tests).
  En producción (Linux) no aplica.
- Actualizar Procrastinate exige revisar si su versión trae migraciones de esquema.
