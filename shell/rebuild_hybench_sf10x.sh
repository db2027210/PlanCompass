#!/usr/bin/env bash
set -euo pipefail

source /home/anonymous/program/resource_adaptive/shell/vm_resource_helper.sh

DB_HOST="${DB_HOST:-192.0.2.8}"
DB_PORT="${DB_PORT:-25432}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-CHANGE_ME}"
DB_NAME="${DB_NAME:-hybench_sf10x}"
SF="${SF:-10x}"
HYBENCH_DIR="${HYBENCH_DIR:-/home/anonymous/HyBench-2024-master}"
WORK_DIR="${WORK_DIR:-/tmp/resource_adaptive_hybench_sf10x_rebuild}"
DATA_DIR="${DATA_DIR:-${HYBENCH_DIR}/Data_${SF}}"
PROPS_FILE="${PROPS_FILE:-${WORK_DIR}/pg_${SF}_docker.props}"
LOAD_SQL="${LOAD_SQL:-${WORK_DIR}/load_data_pg_${SF}_docker.sql}"
DDL_FILE="${DDL_FILE:-${HYBENCH_DIR}/conf/ddl_pg.sql}"
INDEX_FILE="${INDEX_FILE:-${HYBENCH_DIR}/conf/create_index_pg.sql}"
QUIET="${QUIET:-0}"
QUIET_COPY_ONLY="${QUIET_COPY_ONLY:-1}"
MONITOR_PROGRESS="${MONITOR_PROGRESS:-1}"
PROGRESS_INTERVAL_SEC="${PROGRESS_INTERVAL_SEC:-30}"
LOG_DIR="${LOG_DIR:-${WORK_DIR}/logs}"
HYBENCH_SCHEMA_LOG="${HYBENCH_SCHEMA_LOG:-${LOG_DIR}/hybench_schema.log}"
HYBENCH_INDEX_LOG="${HYBENCH_INDEX_LOG:-${LOG_DIR}/hybench_index.log}"
COPY_LOG="${COPY_LOG:-${LOG_DIR}/copy.log}"

mkdir -p "${WORK_DIR}"
mkdir -p "${LOG_DIR}"

export PGPASSWORD="${DB_PASSWORD}"

restore_vm_resources() {
  vm_resource_apply_target_from_config || true
}

trap restore_vm_resources EXIT

psql_postgres() {
  psql "postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/postgres" -X -v ON_ERROR_STOP=1 "$@"
}

psql_target() {
  psql "postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}" -X -v ON_ERROR_STOP=1 "$@"
}

run_psql_postgres() {
  if [ "${QUIET}" = "1" ]; then
    psql_postgres -q "$@" >/dev/null
  else
    psql_postgres "$@"
  fi
}

run_psql_target() {
  if [ "${QUIET}" = "1" ]; then
    psql_target -q "$@" >/dev/null
  else
    psql_target "$@"
  fi
}

copy_quiet_enabled() {
  [ "${QUIET}" = "1" ] || [ "${QUIET_COPY_ONLY}" = "1" ]
}

monitor_copy_progress() {
  local target_pid="$1"
  [ "${MONITOR_PROGRESS}" = "1" ] || return 0
  while kill -0 "${target_pid}" 2>/dev/null; do
    local progress
    progress="$(
      psql_target -Atc \
        "SELECT coalesce(relid::regclass::text, '?') || '|' || coalesce(tuples_processed::text, '0')
         FROM pg_stat_progress_copy LIMIT 1;" 2>/dev/null || true
    )"
    if [ -n "${progress}" ]; then
      local rel_name
      local tuples_done
      rel_name="${progress%%|*}"
      tuples_done="${progress##*|}"
      echo "[rebuild] copy progress: table=${rel_name} tuples=${tuples_done}" >&2
    else
      echo "[rebuild] copy progress: running" >&2
    fi
    sleep "${PROGRESS_INTERVAL_SEC}"
  done
}

write_props() {
  cat > "${PROPS_FILE}" <<EOF
sf=${SF}

db=postgreSQL
classname=org.postgresql.Driver
classname_ap=org.postgresql.Driver

username=${DB_USER}
password=${DB_PASSWORD}

username_ap=${DB_USER}
password_ap=${DB_PASSWORD}

url=jdbc:postgresql://${DB_HOST}:${DB_PORT}/${DB_NAME}
url_tp=jdbc:postgresql://${DB_HOST}:${DB_PORT}/${DB_NAME}
url_ap=jdbc:postgresql://${DB_HOST}:${DB_PORT}/${DB_NAME}

at1_percent=35
at2_percent=25
at3_percent=15
at4_percent=15
at5_percent=7
at6_percent=3

tpclient=64
apclient=8

fresh_interval=20

apRunMins=10
tpRunMins=10
xpRunMins=5

risk_rate=0.1
power_alpha=0.1

apround=1

xtpclient=64
xapclient=4

dist=zipfian
contention_num=10000
EOF
}

write_load_sql() {
  cat > "${LOAD_SQL}" <<EOF
\\copy customer from '${DATA_DIR}/customer.csv' CSV DELIMITER ',' ;
\\copy company from '${DATA_DIR}/company.csv' CSV DELIMITER ',' ;
\\copy transfer from '${DATA_DIR}/transfer.csv' CSV DELIMITER ',' ;
\\copy checking from '${DATA_DIR}/checking.csv' CSV DELIMITER ',' ;
\\copy checkingAccount from '${DATA_DIR}/checkingAccount.csv' CSV DELIMITER ',' ;
\\copy savingAccount from '${DATA_DIR}/savingAccount.csv' CSV DELIMITER ',' ;
\\copy loanApps from '${DATA_DIR}/loanApps.csv' CSV DELIMITER ',' ;
\\copy loanTrans from '${DATA_DIR}/loanTrans.csv' CSV DELIMITER ',' ;
EOF
}

echo "[rebuild] rebuild ${DB_NAME} from HyBench Data_${SF}" >&2
if [ "${QUIET}" = "1" ]; then
  echo "[rebuild] quiet logs: ${LOG_DIR}" >&2
elif [ "${QUIET_COPY_ONLY}" = "1" ]; then
  echo "[rebuild] copy logs redirected to: ${COPY_LOG}" >&2
fi

write_props
write_load_sql

vm_resource_apply_max

run_psql_postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${DB_NAME}';"
run_psql_postgres -c "DROP DATABASE IF EXISTS ${DB_NAME} WITH (FORCE);"
run_psql_postgres -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"

echo "[rebuild] create schema" >&2
if [ "${QUIET}" = "1" ]; then
  (cd "${HYBENCH_DIR}" && bash hybench -t sql -c "${PROPS_FILE}" -f "${DDL_FILE}") >"${HYBENCH_SCHEMA_LOG}" 2>&1
else
  (cd "${HYBENCH_DIR}" && bash hybench -t sql -c "${PROPS_FILE}" -f "${DDL_FILE}")
fi

echo "[rebuild] load data from ${DATA_DIR}" >&2
if copy_quiet_enabled; then
  psql_target -q -f "${LOAD_SQL}" >"${COPY_LOG}" 2>&1 &
  copy_pid="$!"
  monitor_copy_progress "${copy_pid}"
  wait "${copy_pid}"
else
  psql_target -f "${LOAD_SQL}"
fi
if [ "${QUIET_COPY_ONLY}" = "1" ] && [ "${QUIET}" != "1" ]; then
  echo "[rebuild] copy finished, verbose output resumed" >&2
fi

echo "[rebuild] build indexes" >&2
if [ "${QUIET}" = "1" ]; then
  (cd "${HYBENCH_DIR}" && bash hybench -t sql -c "${PROPS_FILE}" -f "${INDEX_FILE}") >"${HYBENCH_INDEX_LOG}" 2>&1
else
  (cd "${HYBENCH_DIR}" && bash hybench -t sql -c "${PROPS_FILE}" -f "${INDEX_FILE}")
fi

echo "[rebuild] install pg_stat_statements and analyze ${DB_NAME}" >&2
run_psql_target -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
run_psql_target -c "ANALYZE;"

echo "[rebuild] ready ${DB_NAME}" >&2
