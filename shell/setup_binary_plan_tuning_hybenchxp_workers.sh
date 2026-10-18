#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PREFIX="${BINARY_PLAN_TUNING_HYBENCHXP_PREFIX:-binary_plan_tuning_hybenchxp}"
BASE_DIR="${BINARY_PLAN_TUNING_HYBENCHXP_BASE:-/disk2/resource_adaptive/binary_plan_tuning_hybenchxp}"
DISK1_BASE="${BINARY_PLAN_TUNING_HYBENCHXP_DISK1_BASE:-/disk1/replay_mysql/resource_adaptive/binary_plan_tuning_hybenchxp}"
IMAGE="${BINARY_PLAN_TUNING_HYBENCHXP_IMAGE:-postgres:17}"
DB_NAME="${BINARY_PLAN_TUNING_HYBENCHXP_DB:-hybench_sf10x}"
TEMPLATE_DB="${BINARY_PLAN_TUNING_HYBENCHXP_TEMPLATE_DB:-hybench_sf10x_base}"
DB_PASSWORD="${BINARY_PLAN_TUNING_HYBENCHXP_PASSWORD:-postgres}"
HYBENCH_DIR="${BINARY_PLAN_TUNING_HYBENCHXP_DIR:-/home/anonymous/HyBench-2024-master}"
HYBENCH_SF="${BINARY_PLAN_TUNING_HYBENCHXP_SF:-10x}"
HYBENCH_DATA_DIR="${BINARY_PLAN_TUNING_HYBENCHXP_DATA_DIR:-${HYBENCH_DIR}/Data_${HYBENCH_SF}}"
CPU_MEMORY_WORKERS="${BINARY_PLAN_TUNING_HYBENCHXP_CPU_MEMORY_WORKERS:-8}"
IO_WORKERS="${BINARY_PLAN_TUNING_HYBENCHXP_IO_WORKERS:-2}"
NONE_WORKERS="${BINARY_PLAN_TUNING_HYBENCHXP_NONE_WORKERS:-4}"
RECREATE="${BINARY_PLAN_TUNING_HYBENCHXP_RECREATE:-0}"
RESET_DATA="${BINARY_PLAN_TUNING_HYBENCHXP_RESET_DATA:-0}"
RELOAD="${BINARY_PLAN_TUNING_HYBENCHXP_RELOAD:-0}"
RECLONE_WORKERS="${BINARY_PLAN_TUNING_HYBENCHXP_RECLONE_WORKERS:-1}"
CLONE_PARALLELISM="${BINARY_PLAN_TUNING_HYBENCHXP_CLONE_PARALLELISM:-4}"
MAIN_PORT="${BINARY_PLAN_TUNING_HYBENCHXP_MAIN_PORT:-56432}"
WORKER_PORT_BASE="${BINARY_PLAN_TUNING_HYBENCHXP_WORKER_PORT_BASE:-${MAIN_PORT}}"
WORKER_CPU="${BINARY_PLAN_TUNING_HYBENCHXP_WORKER_CPUS:-8}"
WORKER_MEMORY="${BINARY_PLAN_TUNING_HYBENCHXP_WORKER_MEMORY:-16g}"
WORKER_WORK_MEM="${BINARY_PLAN_TUNING_HYBENCHXP_WORKER_WORK_MEM:-8MB}"
NONE_WORKER_CPU="${BINARY_PLAN_TUNING_HYBENCHXP_NONE_WORKER_CPUS:-32}"
NONE_WORKER_MEMORY="${BINARY_PLAN_TUNING_HYBENCHXP_NONE_WORKER_MEMORY:-16g}"
MAIN_CPUSET="${BINARY_PLAN_TUNING_HYBENCHXP_MAIN_CPUSET:-96-103}"
if (( CLONE_PARALLELISM < 1 )); then
  CLONE_PARALLELISM=1
fi

MAIN_CONTAINER="${PREFIX}_pg17"
MAIN_DATA="${BASE_DIR}/main/pgdata"
LOG_DIR="${BASE_DIR}/logs"
LOAD_SQL="${LOG_DIR}/load_data_pg_${HYBENCH_SF}.sql"
LOAD_MARKER="${BASE_DIR}/main/.hybench_${HYBENCH_SF}_loaded"

mkdir -p "${MAIN_DATA}" "${LOG_DIR}" "${BASE_DIR}/workers" "${DISK1_BASE}/workers"

if [[ ! -d "${HYBENCH_DIR}" ]]; then
  echo "HyBench directory not found: ${HYBENCH_DIR}" >&2
  exit 2
fi
if [[ ! -d "${HYBENCH_DATA_DIR}" ]]; then
  echo "HyBench data directory not found: ${HYBENCH_DATA_DIR}" >&2
  exit 2
fi

safety_clear_dir() {
  local path="$1"
	case "${path}" in
	  /disk1/*binary_plan_tuning_hybenchxp*|/disk2/*binary_plan_tuning_hybenchxp*)
	    sudo -n rm -rf "${path}"
	    ;;
    *)
      echo "Refusing to remove path outside binary_plan_tuning_hybenchxp data roots: ${path}" >&2
      exit 2
      ;;
  esac
}

container_exists() {
  docker ps -a --format '{{.Names}}' | grep -Fxq "$1"
}

remove_container_if_requested() {
  local name="$1"
  if container_exists "${name}" && [[ "${RECREATE}" == "1" ]]; then
    docker stop -t "${BINARY_PLAN_TUNING_HYBENCHXP_STOP_TIMEOUT:-5}" "${name}" >/dev/null 2>&1 || true
    docker rm "${name}" >/dev/null
  fi
}

env_override_name() {
  local raw="$1"
  raw="${raw^^}"
  raw="${raw//-/_}"
  printf 'BINARY_PLAN_TUNING_HYBENCHXP_CPUSET_%s' "${raw}"
}

worker_cpuset() {
  local name="$1"
  local override_name
  override_name="$(env_override_name "${name}")"
  if [[ -n "${!override_name:-}" ]]; then
    printf '%s' "${!override_name}"
    return 0
  fi
  case "${name}" in
    cpu_mem_w1) printf '16-23' ;;
    cpu_mem_w2) printf '24-31' ;;
    cpu_mem_w3) printf '32-39' ;;
    cpu_mem_w4) printf '40-47' ;;
    cpu_mem_w5) printf '48-55' ;;
    cpu_mem_w6) printf '56-63' ;;
    cpu_mem_w7) printf '64-71' ;;
    cpu_mem_w8) printf '72-79' ;;
    io_disk1) printf '80-87' ;;
    io_disk2) printf '88-95' ;;
    none_w*)
      local idx="${name#none_w}"
      local start=$((104 + (idx - 1) * 32))
      printf '%s-%s' "${start}" "$((start + 31))"
      ;;
    *) printf '' ;;
  esac
}

worker_cpus() {
  local name="$1"
  case "${name}" in
    none_w*) printf '%s' "${NONE_WORKER_CPU}" ;;
    *) printf '%s' "${WORKER_CPU}" ;;
  esac
}

worker_memory() {
  local name="$1"
  case "${name}" in
    none_w*) printf '%s' "${NONE_WORKER_MEMORY}" ;;
    *) printf '%s' "${WORKER_MEMORY}" ;;
  esac
}

docker_limit_args() {
  local cpus="$1"
  local memory="$2"
  local cpuset="$3"
  printf '%s\n' "--cpus=${cpus}"
  printf '%s\n' "--memory=${memory}"
  if [[ -n "${cpuset}" ]]; then
    printf '%s\n' "--cpuset-cpus=${cpuset}"
  fi
}

update_container_limits() {
  local name="$1"
  local cpus="$2"
  local memory="$3"
  local cpuset="$4"
  local args=()
  while IFS= read -r arg; do
    args+=("${arg}")
  done < <(docker_limit_args "${cpus}" "${memory}" "${cpuset}")
  docker update "${args[@]}" "${name}" >/dev/null
}

wait_pg() {
  local port="$1"
  local db="${2:-postgres}"
  local deadline=$((SECONDS + 240))
  until PGPASSWORD="${DB_PASSWORD}" psql -h 127.0.0.1 -p "${port}" -U postgres -d "${db}" -At -c 'SELECT 1;' >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "PostgreSQL did not become ready on 127.0.0.1:${port}/${db}" >&2
      return 1
    fi
    sleep 2
  done
}

start_container() {
  local name="$1"
  local port="$2"
  local data_dir="$3"
  local cpus="${4:-${WORKER_CPU}}"
  local memory="${5:-${WORKER_MEMORY}}"
  local cpuset="${6:-}"

  mkdir -p "${data_dir}"
  remove_container_if_requested "${name}"
  if container_exists "${name}"; then
    update_container_limits "${name}" "${cpus}" "${memory}" "${cpuset}"
    timeout "${BINARY_PLAN_TUNING_HYBENCHXP_DOCKER_START_TIMEOUT:-60}" docker start "${name}" >/dev/null
  else
    local limit_args=()
    while IFS= read -r arg; do
      limit_args+=("${arg}")
    done < <(docker_limit_args "${cpus}" "${memory}" "${cpuset}")
    docker run -d \
      --name "${name}" \
      --hostname "${name}" \
      "${limit_args[@]}" \
      --log-driver=local \
      --log-opt max-size=50m \
      -p "127.0.0.1:${port}:5432" \
      -e POSTGRES_PASSWORD="${DB_PASSWORD}" \
      -e POSTGRES_HOST_AUTH_METHOD=trust \
      -e POSTGRES_DB=postgres \
      -v "${data_dir}:/var/lib/postgresql/data" \
      "${IMAGE}" \
      -c shared_preload_libraries=pg_stat_statements \
      -c track_io_timing=on \
      -c max_parallel_workers=8 \
      -c max_parallel_workers_per_gather=4 \
      -c min_parallel_table_scan_size=1MB \
      -c min_parallel_index_scan_size=512kB \
      -c "work_mem=${WORKER_WORK_MEM}" \
      -c maintenance_work_mem=512MB >/dev/null
  fi
  if [[ -n "${cpuset}" ]]; then
    echo "[hybenchxp-setup] ${name} cpuset=${cpuset} cpus=${cpus} memory=${memory}" >&2
  else
    echo "[hybenchxp-setup] ${name} cpus=${cpus} memory=${memory}" >&2
  fi
  wait_pg "${port}" postgres
}

psql_postgres() {
  PGPASSWORD="${DB_PASSWORD}" psql -h 127.0.0.1 -p "${MAIN_PORT}" -U postgres -d postgres -X -v ON_ERROR_STOP=1 "$@"
}

psql_target() {
  PGPASSWORD="${DB_PASSWORD}" psql -h 127.0.0.1 -p "${MAIN_PORT}" -U postgres -d "${DB_NAME}" -X -v ON_ERROR_STOP=1 "$@"
}

write_load_sql() {
  cat > "${LOAD_SQL}" <<EOF
\\copy customer from '${HYBENCH_DATA_DIR}/customer.csv' CSV DELIMITER ',' ;
\\copy company from '${HYBENCH_DATA_DIR}/company.csv' CSV DELIMITER ',' ;
\\copy transfer from '${HYBENCH_DATA_DIR}/transfer.csv' CSV DELIMITER ',' ;
\\copy checking from '${HYBENCH_DATA_DIR}/checking.csv' CSV DELIMITER ',' ;
\\copy checkingAccount from '${HYBENCH_DATA_DIR}/checkingAccount.csv' CSV DELIMITER ',' ;
\\copy savingAccount from '${HYBENCH_DATA_DIR}/savingAccount.csv' CSV DELIMITER ',' ;
\\copy loanApps from '${HYBENCH_DATA_DIR}/loanApps.csv' CSV DELIMITER ',' ;
\\copy loanTrans from '${HYBENCH_DATA_DIR}/loanTrans.csv' CSV DELIMITER ',' ;
EOF
}

load_hybench_main() {
  if [[ -e "${LOAD_MARKER}" && "${RELOAD}" != "1" ]]; then
    echo "[hybenchxp-setup] main database already loaded: ${LOAD_MARKER}" >&2
    ensure_template_on_port "${MAIN_PORT}"
    return 0
  fi

  write_load_sql
  local ddl_log="${LOG_DIR}/hybench_schema_$(date +%Y%m%d_%H%M%S).log"
  local load_log="${LOG_DIR}/hybench_load_$(date +%Y%m%d_%H%M%S).log"
  local index_log="${LOG_DIR}/hybench_index_$(date +%Y%m%d_%H%M%S).log"

  echo "[hybenchxp-setup] rebuilding ${DB_NAME} in ${MAIN_CONTAINER}" >&2
  psql_postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${DB_NAME}';" >/dev/null
  psql_postgres -c "DROP DATABASE IF EXISTS ${DB_NAME} WITH (FORCE);"
  psql_postgres -c "CREATE DATABASE ${DB_NAME} OWNER postgres;"
  psql_target -f "${HYBENCH_DIR}/conf/ddl_pg.sql" >"${ddl_log}" 2>&1
  psql_target -f "${LOAD_SQL}" >"${load_log}" 2>&1
  psql_target -f "${HYBENCH_DIR}/conf/create_index_pg.sql" >"${index_log}" 2>&1
  psql_target -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
  psql_target -c "ANALYZE;"
  ensure_template_on_port "${MAIN_PORT}"
  date +%Y-%m-%dT%H:%M:%S > "${LOAD_MARKER}"
  echo "[hybenchxp-setup] main database loaded; logs=${LOG_DIR}" >&2
}

ensure_template_on_port() {
  local port="$1"
  local exists
  exists="$(PGPASSWORD="${DB_PASSWORD}" psql -h 127.0.0.1 -p "${port}" -U postgres -d postgres -At -c "SELECT 1 FROM pg_database WHERE datname='${TEMPLATE_DB}' LIMIT 1;" 2>/dev/null || true)"
  if [[ "${exists}" == "1" ]]; then
    return 0
  fi
  echo "[hybenchxp-setup] create template ${TEMPLATE_DB} on port ${port}" >&2
  PGPASSWORD="${DB_PASSWORD}" psql -h 127.0.0.1 -p "${port}" -U postgres -d postgres -X -v ON_ERROR_STOP=1 \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('${DB_NAME}', '${TEMPLATE_DB}');" \
    -c "DROP DATABASE IF EXISTS ${TEMPLATE_DB} WITH (FORCE);" \
    -c "CREATE DATABASE ${TEMPLATE_DB} WITH TEMPLATE ${DB_NAME} OWNER postgres;" \
    -c "ALTER DATABASE ${TEMPLATE_DB} WITH IS_TEMPLATE true ALLOW_CONNECTIONS false;"
}

clone_pgdata() {
  local src="$1"
  local dst="$2"
  local marker="$3"
  if [[ -e "${marker}" && "${RECLONE_WORKERS}" != "1" ]]; then
    echo "[hybenchxp-setup] worker clone already exists: ${marker}" >&2
    return 0
  fi
  case "${src}" in
    /disk1/*binary_plan_tuning_hybenchxp*|/disk2/*binary_plan_tuning_hybenchxp*) ;;
    *)
      echo "Refusing to clone from unexpected source: ${src}" >&2
      exit 2
      ;;
  esac
  echo "[hybenchxp-setup] clone ${src} -> ${dst}" >&2
  mkdir -p "$(dirname "${dst}")"
  local dst_parent
  local dst_base
  dst_parent="$(dirname "${dst}")"
  dst_base="$(basename "${dst}")"
  sudo -n rm -rf "${dst}" "${dst}.tmp"
  sudo -n cp -a --reflink=auto "${src}" "${dst}.tmp" 2>/dev/null || sudo -n cp -a "${src}" "${dst}.tmp"
  sudo -n rm -f "${dst}.tmp/postmaster.pid"
  sudo -n mv "${dst}.tmp" "${dst}"
  date +%Y-%m-%dT%H:%M:%S > "${marker}"
}

worker_data_dir() {
  local name="$1"
  case "${name}" in
    io_disk1) echo "${DISK1_BASE}/workers/io_disk1/pgdata" ;;
    io_disk2) echo "${BASE_DIR}/workers/io_disk2/pgdata" ;;
    cpu_mem_w*) echo "${BASE_DIR}/workers/${name}/pgdata" ;;
    none_w*) echo "${BASE_DIR}/workers/${name}/pgdata" ;;
    *) echo "${BASE_DIR}/workers/${name}/pgdata" ;;
  esac
}

worker_port() {
  local name="$1"
  case "${name}" in
    cpu_mem_w*)
      local idx="${name#cpu_mem_w}"
      echo $((WORKER_PORT_BASE + idx))
      ;;
    io_disk1) echo $((WORKER_PORT_BASE + 9)) ;;
    io_disk2) echo $((WORKER_PORT_BASE + 10)) ;;
    none_w*)
      local idx="${name#none_w}"
      echo $((WORKER_PORT_BASE + 10 + idx))
      ;;
    *) return 1 ;;
  esac
}

all_worker_names=()
for idx in $(seq 1 "${CPU_MEMORY_WORKERS}"); do
  all_worker_names+=("cpu_mem_w${idx}")
done
if (( IO_WORKERS >= 1 )); then
  all_worker_names+=("io_disk1")
fi
if (( IO_WORKERS >= 2 )); then
  all_worker_names+=("io_disk2")
fi
for idx in $(seq 1 "${NONE_WORKERS}"); do
  all_worker_names+=("none_w${idx}")
done

remove_container_if_requested "${MAIN_CONTAINER}"
for worker_name in "${all_worker_names[@]}"; do
  remove_container_if_requested "${PREFIX}_${worker_name}"
done

if [[ "${RECREATE}" == "1" && "${RESET_DATA}" == "1" ]]; then
  safety_clear_dir "${MAIN_DATA}"
  for worker_name in "${all_worker_names[@]}"; do
    safety_clear_dir "$(worker_data_dir "${worker_name}")"
  done
  rm -f "${LOAD_MARKER}"
fi

start_container "${MAIN_CONTAINER}" "${MAIN_PORT}" "${MAIN_DATA}" \
  "${BINARY_PLAN_TUNING_HYBENCHXP_MAIN_CPUS:-8}" \
  "${BINARY_PLAN_TUNING_HYBENCHXP_MAIN_MEMORY:-8g}" \
  "${MAIN_CPUSET}"
load_hybench_main

echo "[hybenchxp-setup] stopping main for consistent PGDATA clone" >&2
docker stop "${MAIN_CONTAINER}" >/dev/null

clone_pids=()
clone_failed=0
for worker_name in "${all_worker_names[@]}"; do
  (
    data_dir="$(worker_data_dir "${worker_name}")"
    marker="$(dirname "${data_dir}")/.cloned_from_main_${HYBENCH_SF}"
    clone_pgdata "${MAIN_DATA}" "${data_dir}" "${marker}"
  ) &
  clone_pids+=("$!")
  if (( ${#clone_pids[@]} >= CLONE_PARALLELISM )); then
    if ! wait "${clone_pids[0]}"; then
      clone_failed=1
    fi
    clone_pids=("${clone_pids[@]:1}")
  fi
done
for clone_pid in "${clone_pids[@]}"; do
  if ! wait "${clone_pid}"; then
    clone_failed=1
  fi
done
if (( clone_failed != 0 )); then
  echo "[hybenchxp-setup] worker PGDATA clone failed" >&2
  exit 1
fi

start_container "${MAIN_CONTAINER}" "${MAIN_PORT}" "${MAIN_DATA}" \
  "${BINARY_PLAN_TUNING_HYBENCHXP_MAIN_CPUS:-8}" \
  "${BINARY_PLAN_TUNING_HYBENCHXP_MAIN_MEMORY:-8g}" \
  "${MAIN_CPUSET}"
wait_pg "${MAIN_PORT}" "${DB_NAME}"
ensure_template_on_port "${MAIN_PORT}"

for worker_name in "${all_worker_names[@]}"; do
  data_dir="$(worker_data_dir "${worker_name}")"
  port="$(worker_port "${worker_name}")"
  start_container "${PREFIX}_${worker_name}" "${port}" "${data_dir}" "$(worker_cpus "${worker_name}")" "$(worker_memory "${worker_name}")" "$(worker_cpuset "${worker_name}")"
  wait_pg "${port}" "${DB_NAME}"
  ensure_template_on_port "${port}"
done

cat <<EOF
binary_plan_tuning_hybenchxp Docker worker pool is ready.
main=127.0.0.1:${MAIN_PORT}/${DB_NAME}
cpu_memory_workers=${CPU_MEMORY_WORKERS}
io_workers=${IO_WORKERS}
none_workers=${NONE_WORKERS}
main_data=${MAIN_DATA}
disk1_base=${DISK1_BASE}
disk2_base=${BASE_DIR}
logs=${LOG_DIR}
config=${ROOT_DIR}/config/binary_plan_tuning_hybenchxp.json
main_cpuset=${MAIN_CPUSET}
cpu_mem_w1_cpuset=$(worker_cpuset cpu_mem_w1)
cpu_mem_w2_cpuset=$(worker_cpuset cpu_mem_w2)
cpu_mem_w3_cpuset=$(worker_cpuset cpu_mem_w3)
cpu_mem_w4_cpuset=$(worker_cpuset cpu_mem_w4)
cpu_mem_w5_cpuset=$(worker_cpuset cpu_mem_w5)
cpu_mem_w6_cpuset=$(worker_cpuset cpu_mem_w6)
cpu_mem_w7_cpuset=$(worker_cpuset cpu_mem_w7)
cpu_mem_w8_cpuset=$(worker_cpuset cpu_mem_w8)
io_disk1_cpuset=$(worker_cpuset io_disk1)
io_disk2_cpuset=$(worker_cpuset io_disk2)
EOF
