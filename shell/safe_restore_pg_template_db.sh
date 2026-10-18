#!/usr/bin/env bash
set -euo pipefail

host="${DB_HOST:-127.0.0.1}"
port="${DB_PORT:?DB_PORT is required}"
user="${DB_USER:-postgres}"
password="${DB_PASSWORD:-postgres}"
database="${DB_NAME:?DB_NAME is required}"
template_database="${TEMPLATE_DB:?TEMPLATE_DB is required}"

export PGPASSWORD="${password}"

psql -h "${host}" -p "${port}" -U "${user}" -d postgres \
    -v ON_ERROR_STOP=1 \
    -v target_db="${database}" \
    -v template_db="${template_database}" <<'SQL'
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = :'target_db'
  AND pid <> pg_backend_pid();

SELECT format('DROP DATABASE IF EXISTS %I', :'target_db') \gexec
SELECT format('CREATE DATABASE %I TEMPLATE %I', :'target_db', :'template_db') \gexec
SQL

psql -h "${host}" -p "${port}" -U "${user}" -d "${database}" \
    -v ON_ERROR_STOP=1 -Atc \
    "select current_database(), current_setting('server_version'), current_setting('shared_preload_libraries');"
