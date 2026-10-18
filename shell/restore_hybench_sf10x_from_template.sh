#!/usr/bin/env bash
set -euo pipefail

source /home/anonymous/program/resource_adaptive/shell/vm_resource_helper.sh

DB_HOST="${DB_HOST:-192.0.2.8}"
DB_PORT="${DB_PORT:-25432}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-CHANGE_ME}"
DB_NAME="${DB_NAME:-hybench_sf10x}"
TEMPLATE_DB="${TEMPLATE_DB:-hybench_sf10x_base}"
QUIET="${QUIET:-0}"
QUIET_COPY_ONLY="${QUIET_COPY_ONLY:-1}"
MONITOR_PROGRESS="${MONITOR_PROGRESS:-1}"
PROGRESS_INTERVAL_SEC="${PROGRESS_INTERVAL_SEC:-30}"

export PGPASSWORD="${DB_PASSWORD}"

restore_vm_resources() {
  local original_status=$?
  trap - EXIT
  if ! vm_resource_apply_target_from_config; then
    echo "[restore] failed to restore target VM profile" >&2
    exit 1
  fi
  exit "${original_status}"
}

trap restore_vm_resources EXIT

psql_postgres() {
  psql "postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/postgres" -X -v ON_ERROR_STOP=1 "$@"
}

run_psql_postgres() {
  if [ "${QUIET}" = "1" ]; then
    psql_postgres -q "$@" >/dev/null
  else
    psql_postgres "$@"
  fi
}

copy_quiet_enabled() {
  [ "${QUIET}" = "1" ] || [ "${QUIET_COPY_ONLY}" = "1" ]
}

monitor_clone_progress() {
  local target_pid="$1"
  local clone_sql="CREATE DATABASE ${DB_NAME} WITH TEMPLATE ${TEMPLATE_DB} OWNER ${DB_USER};"
  [ "${MONITOR_PROGRESS}" = "1" ] || return 0
  while kill -0 "${target_pid}" 2>/dev/null; do
    local progress
    progress="$(
      psql_postgres -Atc \
        "SELECT coalesce((now() - query_start)::text, '0') || '|' || \
                coalesce(wait_event_type, '') || '|' || coalesce(wait_event, '') \
         FROM pg_stat_activity \
         WHERE query = '${clone_sql}' \
         LIMIT 1;" 2>/dev/null || true
    )"
    if [ -n "${progress}" ]; then
      local age
      local wait_type
      local wait_event
      age="${progress%%|*}"
      progress="${progress#*|}"
      wait_type="${progress%%|*}"
      wait_event="${progress#*|}"
      echo "[restore] clone progress: age=${age} wait=${wait_type}/${wait_event}" >&2
    else
      echo "[restore] clone progress: running" >&2
    fi
    sleep "${PROGRESS_INTERVAL_SEC}"
  done
}

if ! psql_postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${TEMPLATE_DB}'" | grep -qx 1; then
  echo "template database ${TEMPLATE_DB} does not exist; run shell/bootstrap_hybench_sf10x_template.sh first" >&2
  exit 2
fi

echo "[restore] clone ${TEMPLATE_DB} -> ${DB_NAME}" >&2
vm_resource_apply_max
run_psql_postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('${DB_NAME}', '${TEMPLATE_DB}');"
run_psql_postgres -c "DROP DATABASE IF EXISTS ${DB_NAME} WITH (FORCE);"
if copy_quiet_enabled; then
  psql_postgres -q -c "CREATE DATABASE ${DB_NAME} WITH TEMPLATE ${TEMPLATE_DB} OWNER ${DB_USER};" >/dev/null &
  clone_pid="$!"
  monitor_clone_progress "${clone_pid}"
  wait "${clone_pid}"
else
  psql_postgres -c "CREATE DATABASE ${DB_NAME} WITH TEMPLATE ${TEMPLATE_DB} OWNER ${DB_USER};"
fi
if [ "${QUIET}" = "1" ]; then
  psql "postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}" -X -q -v ON_ERROR_STOP=1 \
    -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;" >/dev/null
else
  psql "postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}" -X -v ON_ERROR_STOP=1 \
    -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
fi
if [ "${QUIET_COPY_ONLY}" = "1" ] && [ "${QUIET}" != "1" ]; then
  echo "[restore] clone finished, verbose output resumed" >&2
fi
echo "[restore] ready ${DB_NAME}" >&2
