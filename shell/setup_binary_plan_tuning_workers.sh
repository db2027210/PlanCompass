#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP_ONE="${ROOT_DIR}/shell/setup_binary_plan_tuning_docker.sh"

IMAGE="${BINARY_PLAN_TUNING_IMAGE:-postgres:17}"
DB_NAME="${BINARY_PLAN_TUNING_DB:-hybench_sf10x}"
DB_PASSWORD="${BINARY_PLAN_TUNING_PASSWORD:-postgres}"
RECREATE="${BINARY_PLAN_TUNING_RECREATE:-0}"
RESET_DATA="${BINARY_PLAN_TUNING_RESET_DATA:-1}"
CPU_MEMORY_WORKERS="${BINARY_PLAN_TUNING_CPU_MEMORY_WORKERS:-8}"
IO_WORKERS="${BINARY_PLAN_TUNING_IO_WORKERS:-2}"
BOOTSTRAP_PARALLELISM="${BINARY_PLAN_TUNING_BOOTSTRAP_PARALLELISM:-4}"
HYBENCH_DIR="${HYBENCH_DIR:-/home/anonymous/HyBench-2024-master}"
FORCE_BOOTSTRAP="${BINARY_PLAN_TUNING_FORCE_HYBENCH_BOOTSTRAP:-0}"

worker_specs=()

start_one() {
  local name="$1"
  local port="$2"
  local base_dir="$3"
  local cpus="${4:-4}"
  local memory="${5:-6g}"

  BINARY_PLAN_TUNING_IMAGE="${IMAGE}" \
  BINARY_PLAN_TUNING_DB=postgres \
  BINARY_PLAN_TUNING_PASSWORD="${DB_PASSWORD}" \
  BINARY_PLAN_TUNING_RECREATE="${RECREATE}" \
  BINARY_PLAN_TUNING_RESET_DATA="${RESET_DATA}" \
  BINARY_PLAN_TUNING_SKIP_SCHEMA=1 \
  BINARY_PLAN_TUNING_CONTAINER="${name}" \
  BINARY_PLAN_TUNING_PORT="${port}" \
  BINARY_PLAN_TUNING_BASE="${base_dir}" \
  BINARY_PLAN_TUNING_CPUS="${cpus}" \
  BINARY_PLAN_TUNING_MEMORY="${memory}" \
    "${SETUP_ONE}"
}

register_worker() {
  local name="$1"
  local port="$2"
  local base_dir="$3"
  worker_specs+=("${name}|${port}|${base_dir}")
}

hybench_ready() {
  local port="$1"
  PGPASSWORD="${DB_PASSWORD}" psql -h 127.0.0.1 -p "${port}" -U postgres -d "${DB_NAME}" -At \
    -c "SELECT to_regclass('public.customer') IS NOT NULL AND to_regclass('public.transfer') IS NOT NULL;" \
    2>/dev/null | grep -qx t
}

bootstrap_one() {
  local name="$1"
  local port="$2"
  local base_dir="$3"
  local work_dir="${base_dir}/hybench_bootstrap"
  local log_dir="${base_dir}/logs/hybench_bootstrap"
  mkdir -p "${work_dir}" "${log_dir}"
  if [[ "${FORCE_BOOTSTRAP}" != "1" ]] && hybench_ready "${port}"; then
    echo "[hybench-bootstrap] ${name} already has ${DB_NAME}; skip" >&2
    return 0
  fi
  echo "[hybench-bootstrap] loading ${DB_NAME} into ${name} on port ${port}" >&2
  env -u OPTIMIZER_CONFIG_PATH \
    DB_HOST=127.0.0.1 \
    DB_PORT="${port}" \
    DB_USER=postgres \
    DB_PASSWORD="${DB_PASSWORD}" \
    DB_NAME="${DB_NAME}" \
    TEMPLATE_DB="${DB_NAME}_base" \
    HYBENCH_DIR="${HYBENCH_DIR}" \
    WORK_DIR="${work_dir}" \
    LOG_DIR="${log_dir}" \
    QUIET=1 \
    QUIET_COPY_ONLY=1 \
    MONITOR_PROGRESS=1 \
    PROGRESS_INTERVAL_SEC=60 \
      "${ROOT_DIR}/shell/bootstrap_hybench_sf10x_template.sh" \
      >"${log_dir}/bootstrap.stdout.log" 2>"${log_dir}/bootstrap.stderr.log"
  echo "[hybench-bootstrap] ready ${name}" >&2
}

wait_for_bootstrap_slot() {
  while (( $(jobs -rp | wc -l) >= BOOTSTRAP_PARALLELISM )); do
    sleep 5
  done
}

echo "Starting binary_plan_tuning main PostgreSQL container..." >&2
start_one \
  "binary_plan_tuning_pg17" \
  "55432" \
  "/disk2/resource_adaptive/binary_plan_tuning" \
  "${BINARY_PLAN_TUNING_MAIN_CPUS:-8}" \
  "${BINARY_PLAN_TUNING_MAIN_MEMORY:-8g}"
register_worker "binary_plan_tuning_pg17" "55432" "/disk2/resource_adaptive/binary_plan_tuning"

for idx in $(seq 1 "${CPU_MEMORY_WORKERS}"); do
  port=$((55432 + idx))
  echo "Starting binary_plan_tuning CPU/Memory worker ${idx} on port ${port}..." >&2
  start_one \
    "binary_plan_tuning_cpu_mem_w${idx}" \
    "${port}" \
    "/disk2/resource_adaptive/binary_plan_tuning/workers/cpu_mem_w${idx}" \
    "${BINARY_PLAN_TUNING_WORKER_CPUS:-8}" \
    "${BINARY_PLAN_TUNING_WORKER_MEMORY:-8g}"
  register_worker \
    "binary_plan_tuning_cpu_mem_w${idx}" \
    "${port}" \
    "/disk2/resource_adaptive/binary_plan_tuning/workers/cpu_mem_w${idx}"
done

if (( IO_WORKERS >= 1 )); then
  echo "Starting binary_plan_tuning IO worker on /disk1..." >&2
  if [[ ! -d /disk1 ]]; then
    echo "Refusing to start io_disk1 because /disk1 does not exist." >&2
    exit 2
  fi
  start_one \
    "binary_plan_tuning_io_disk1" \
    "55441" \
    "/disk1/resource_adaptive/binary_plan_tuning/workers/io_disk1" \
    "${BINARY_PLAN_TUNING_IO_WORKER_CPUS:-8}" \
    "${BINARY_PLAN_TUNING_IO_WORKER_MEMORY:-8g}"
  register_worker \
    "binary_plan_tuning_io_disk1" \
    "55441" \
    "/disk1/resource_adaptive/binary_plan_tuning/workers/io_disk1"
fi

if (( IO_WORKERS >= 2 )); then
  echo "Starting binary_plan_tuning IO worker on /disk2..." >&2
  start_one \
    "binary_plan_tuning_io_disk2" \
    "55442" \
    "/disk2/resource_adaptive/binary_plan_tuning/workers/io_disk2" \
    "${BINARY_PLAN_TUNING_IO_WORKER_CPUS:-8}" \
    "${BINARY_PLAN_TUNING_IO_WORKER_MEMORY:-8g}"
  register_worker \
    "binary_plan_tuning_io_disk2" \
    "55442" \
    "/disk2/resource_adaptive/binary_plan_tuning/workers/io_disk2"
fi

echo "Bootstrapping HyBench XP data with parallelism=${BOOTSTRAP_PARALLELISM}..." >&2
for spec in "${worker_specs[@]}"; do
  wait_for_bootstrap_slot
  IFS='|' read -r name port base_dir <<<"${spec}"
  bootstrap_one "${name}" "${port}" "${base_dir}" &
done
wait

cat <<EOF
binary_plan_tuning Docker worker pool is ready.
main=127.0.0.1:55432
cpu_memory_workers=${CPU_MEMORY_WORKERS}
io_workers=${IO_WORKERS}
data_roots=/disk1,/disk2
database=${DB_NAME}
EOF
