#!/bin/sh
# Se ejecuta una sola vez, al inicializar un volumen de datos vacío, como superusuario.
# Crea los dos roles de la app (ver docs/adr/004) y la extensión pgvector, que requiere
# superusuario. Todo lo demás (tablas, RLS, cola) lo crean las migraciones de Alembic.
set -eu

: "${POSTGRES_APP_USER:?definí POSTGRES_APP_USER}"
: "${POSTGRES_APP_PASSWORD:?definí POSTGRES_APP_PASSWORD}"
: "${POSTGRES_MIGRATOR_USER:?definí POSTGRES_MIGRATOR_USER}"
: "${POSTGRES_MIGRATOR_PASSWORD:?definí POSTGRES_MIGRATOR_PASSWORD}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v db="$POSTGRES_DB" \
    -v app_user="$POSTGRES_APP_USER" \
    -v app_password="$POSTGRES_APP_PASSWORD" \
    -v migrator_user="$POSTGRES_MIGRATOR_USER" \
    -v migrator_password="$POSTGRES_MIGRATOR_PASSWORD" <<'EOSQL'
CREATE EXTENSION IF NOT EXISTS vector;

-- Dueño del esquema: ejecuta las migraciones. No es superusuario ni saltea RLS.
CREATE ROLE :"migrator_user" LOGIN PASSWORD :'migrator_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- Rol de la app (API y worker): solo DML, nunca dueño de tablas, siempre sujeto a RLS.
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO :"migrator_user", :"app_user";

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO :"migrator_user";
GRANT USAGE ON SCHEMA public TO :"app_user";

-- Permisos sobre todo lo que cree el rol de migraciones en el futuro.
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user"
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user" IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO :"app_user";
EOSQL
