#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNS_DIR="${BINARY_PLAN_TUNING_HYBENCHXP_RUNS_DIR:-/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs}"
WAITER_DIR="${BINARY_PLAN_TUNING_HYBENCHXP_WAITER_DIR:-/disk2/resource_adaptive/hybenchxp_waiter}"
BLOCK_FILE="${HYBENCHXP_RUNXP_BLOCK_FILE:-/tmp/bonect_block_external_runxp}"

print_section() {
  printf '\n== %s ==\n' "$1"
}

print_section "branch"
git -C "${ROOT_DIR}" branch --show-current 2>/dev/null || true

print_section "external blockers"
if [[ -f "${BLOCK_FILE}" ]]; then
  printf 'block_file=%s\n' "${BLOCK_FILE}"
  sed -n '1,5p' "${BLOCK_FILE}" 2>/dev/null || true
else
  printf 'block_file_absent=%s\n' "${BLOCK_FILE}"
fi
ps -eo pid,ppid,pgid,sid,etime,cmd \
  | grep -E 'pg_bonect_phase2|bonect_external_xp_guard|run_pg_benchmark_bottleneck_matrix.py|parameter_optimizer.py|HyBench|runap|runxp' \
  | grep -v -E 'grep -E|status_binary_plan_tuning_hybenchxp.sh' || true

print_section "waiters"
if [[ -d "${WAITER_DIR}" ]]; then
  find "${WAITER_DIR}" -maxdepth 1 -type f -printf '%TY-%Tm-%Td %TH:%TM:%TS %s %p\n' | sort | tail -10
  for pid_file in "${WAITER_DIR}"/latest_*.pid; do
    [[ -f "${pid_file}" ]] || continue
    pid="$(sed -n '1p' "${pid_file}")"
    printf 'pid_file=%s pid=%s\n' "${pid_file}" "${pid}"
    ps -fp "${pid}" 2>/dev/null || true
  done
  for log_file in "${WAITER_DIR}"/latest_*.log; do
    [[ -f "${log_file}" ]] || continue
    actual_log="$(sed -n '1p' "${log_file}")"
    printf 'log_pointer=%s actual=%s\n' "${log_file}" "${actual_log}"
    tail -8 "${actual_log}" 2>/dev/null || true
  done
else
  printf 'waiter_dir_absent=%s\n' "${WAITER_DIR}"
fi

print_section "docker workers"
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}' \
  | grep -E 'binary_plan_tuning_hybenchxp|postgres|hybench' || true

print_section "latest binary-plan runs"
if [[ -d "${RUNS_DIR}" ]]; then
  find "${RUNS_DIR}" -maxdepth 1 -type d -printf '%TY-%Tm-%Td %TH:%TM:%TS %p\n' | sort | tail -10
  for file in endpoint_screening_hybenchxp.jsonl cpu_binary_search_hybenchxp.jsonl memory_binary_search_hybenchxp.jsonl io_binary_search_hybenchxp.jsonl; do
    latest="$(find "${RUNS_DIR}" -maxdepth 2 -type f -name "${file}" -printf '%TY-%Tm-%Td %TH:%TM:%TS %p\n' 2>/dev/null | sort | tail -1 | awk '{print $3}')"
    if [[ -n "${latest}" ]]; then
      printf '%s latest=%s lines=' "${file}" "${latest}"
      wc -l < "${latest}" 2>/dev/null || true
      tail -1 "${latest}" 2>/dev/null || true
    else
      printf '%s latest=missing\n' "${file}"
    fi
  done
else
  printf 'runs_dir_absent=%s\n' "${RUNS_DIR}"
fi

print_section "reports"
find "${ROOT_DIR}/report/bonect" -maxdepth 1 -type f \
  \( -name 'binary_plan_resource_tuning_hybenchxp*.md' -o -name '*hybenchxp*run_audit*.md' \) \
  -printf '%TY-%Tm-%Td %TH:%TM:%TS %p\n' | sort | tail -10
