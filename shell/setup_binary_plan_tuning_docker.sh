#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${BINARY_PLAN_TUNING_BASE:-/disk2/resource_adaptive/binary_plan_tuning}"
CONTAINER="${BINARY_PLAN_TUNING_CONTAINER:-binary_plan_tuning_pg17}"
IMAGE="${BINARY_PLAN_TUNING_IMAGE:-postgres:17}"
PORT="${BINARY_PLAN_TUNING_PORT:-55432}"
DB_NAME="${BINARY_PLAN_TUNING_DB:-binary_plan_tuning}"
DB_PASSWORD="${BINARY_PLAN_TUNING_PASSWORD:-postgres}"
PGDATA_DIR="${BASE_DIR}/pgdata"
LOG_DIR="${BASE_DIR}/logs"
SCHEMA_FILE="${BINARY_PLAN_TUNING_SCHEMA:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/shell/binary_plan_tuning_schema.sql}"
RECREATE="${BINARY_PLAN_TUNING_RECREATE:-0}"
RESET_DATA="${BINARY_PLAN_TUNING_RESET_DATA:-1}"
SKIP_SCHEMA="${BINARY_PLAN_TUNING_SKIP_SCHEMA:-0}"

mkdir -p "${PGDATA_DIR}" "${LOG_DIR}"

reset_pgdata_if_requested() {
  if [[ "${RECREATE}" != "1" || "${RESET_DATA}" != "1" ]]; then
    return 0
  fi
  case "${PGDATA_DIR}" in
    /disk1/*binary_plan_tuning*/pgdata|/disk2/*binary_plan_tuning*/pgdata)
      docker run --rm --user 0:0 --entrypoint bash \
        -v "${PGDATA_DIR}:/target" \
        "${IMAGE}" \
        -lc 'find /target -mindepth 1 -maxdepth 1 -exec rm -rf {} +'
      ;;
    *)
      echo "Refusing to clear PGDATA outside binary_plan_tuning data dir: ${PGDATA_DIR}" >&2
      exit 2
      ;;
  esac
}

if docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER}"; then
  if [[ "${RECREATE}" == "1" ]]; then
    docker stop "${CONTAINER}" >/dev/null 2>&1 || true
    docker rm "${CONTAINER}" >/dev/null
  else
    docker start "${CONTAINER}" >/dev/null
  fi
fi

reset_pgdata_if_requested

if ! docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER}"; then
  docker run -d \
    --name "${CONTAINER}" \
    --hostname "${CONTAINER}" \
    --cpus="${BINARY_PLAN_TUNING_CPUS:-8}" \
    --memory="${BINARY_PLAN_TUNING_MEMORY:-8g}" \
    --log-driver=local \
    --log-opt max-size=50m \
    -p "127.0.0.1:${PORT}:5432" \
    -e POSTGRES_PASSWORD="${DB_PASSWORD}" \
    -e POSTGRES_HOST_AUTH_METHOD=trust \
    -e POSTGRES_DB="${DB_NAME}" \
    -v "${PGDATA_DIR}:/var/lib/postgresql/data" \
    "${IMAGE}" \
    -c shared_preload_libraries=pg_stat_statements \
    -c track_io_timing=on \
    -c max_parallel_workers=8 \
    -c max_parallel_workers_per_gather=4 \
    -c min_parallel_table_scan_size=1MB \
    -c min_parallel_index_scan_size=512kB \
    -c work_mem=8MB \
    -c maintenance_work_mem=256MB >/dev/null
fi

deadline=$((SECONDS + 180))
until PGPASSWORD="${DB_PASSWORD}" psql -h 127.0.0.1 -p "${PORT}" -U postgres -d "${DB_NAME}" -At -c 'SELECT 1;' >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    docker logs "${CONTAINER}" >&2 || true
    echo "PostgreSQL did not become ready on 127.0.0.1:${PORT}" >&2
    exit 1
  fi
  sleep 2
done

if [[ "${SKIP_SCHEMA}" != "1" ]]; then
  SCHEMA_LOG="${LOG_DIR}/schema_load_$(date +%Y%m%d_%H%M%S).log"
  PGPASSWORD="${DB_PASSWORD}" psql -h 127.0.0.1 -p "${PORT}" -U postgres -d "${DB_NAME}" -v ON_ERROR_STOP=1 -f "${SCHEMA_FILE}" \
    >"${SCHEMA_LOG}" 2>&1
  cat "${SCHEMA_LOG}"
fi

cat <<EOF
binary_plan_tuning Docker environment is ready.
container=${CONTAINER}
port=${PORT}
database=${DB_NAME}
pgdata=${PGDATA_DIR}
logs=${LOG_DIR}
EOF
