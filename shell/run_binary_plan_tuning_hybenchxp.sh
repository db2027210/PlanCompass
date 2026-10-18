#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${BINARY_PLAN_TUNING_HYBENCHXP_CONFIG:-${ROOT_DIR}/config/binary_plan_tuning_hybenchxp.json}"
RUN_DIR="${BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR:-/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/$(date +%Y%m%d_%H%M%S)}"
PYTHON_BIN="${PYTHON:-python3}"
CMD="${1:-all}"

if [[ "${BINARY_PLAN_TUNING_HYBENCHXP_SKIP_PREPARE:-0}" != "1" ]]; then
  "${ROOT_DIR}/shell/prepare_hybenchxp_runtime.sh" >&2
fi

run_tuner() {
  "${PYTHON_BIN}" "${ROOT_DIR}/binary_plan_resource_tuner.py" \
    --config "${CONFIG}" \
    --output-dir "${RUN_DIR}" \
    "$@"
}

run_stage() {
  local target="$1"
  shift || true
  local args=(binary-search --target "${target}")
  if [[ -n "${BINARY_PLAN_TUNING_HYBENCHXP_MAX_ROUNDS:-}" ]]; then
    args+=(--max-rounds "${BINARY_PLAN_TUNING_HYBENCHXP_MAX_ROUNDS}")
  fi
  if [[ -n "${BINARY_PLAN_TUNING_HYBENCHXP_MAX_CANDIDATES:-}" ]]; then
    args+=(--max-candidates "${BINARY_PLAN_TUNING_HYBENCHXP_MAX_CANDIDATES}")
  fi
  if [[ "${BINARY_PLAN_TUNING_HYBENCHXP_RESUME:-0}" == "1" ]]; then
    args+=(--resume)
  fi
  run_tuner "${args[@]}" "$@"
}

rerun_memory_before_io_if_requested() {
  local marker="${RUN_DIR}/post_none_rerun_memory_before_io.required"
  local done="${RUN_DIR}/post_none_rerun_memory_before_io.done"
  if [[ ! -f "${marker}" || -f "${done}" ]]; then
    return 0
  fi

  local ts memory_run_dir original_run_dir
  ts="$(date +%Y%m%dT%H%M%S)"
  memory_run_dir="${RUN_DIR}/memory_post_none_rerun_${ts}"
  mkdir -p "${memory_run_dir}"

  echo "[hybenchxp] post-none memory rerun requested before io; output_dir=${memory_run_dir}" >&2
  printf '{"started_at":"%s","reason":"post_none_memory_rerun_before_io","output_dir":"%s","preserve_existing_results":true}\n' \
    "${ts}" "${memory_run_dir}" > "${RUN_DIR}/post_none_memory_rerun_started_${ts}.json"

  echo "[hybenchxp] running fresh memory stage before io without moving existing artifacts" >&2
  original_run_dir="${RUN_DIR}"
  RUN_DIR="${memory_run_dir}"
  BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR="${memory_run_dir}" \
  BINARY_PLAN_TUNING_HYBENCHXP_RESUME=0 \
    run_stage memory
  RUN_DIR="${original_run_dir}"

  printf '{"completed_at":"%s","reason":"post_none_memory_rerun_before_io","output_dir":"%s","preserve_existing_results":true}\n' \
    "$(date +%Y%m%dT%H%M%S)" "${memory_run_dir}" > "${done}"
  echo "[hybenchxp] post-none memory rerun completed; continuing io" >&2
}

run_report() {
  run_tuner report --repo-report "report/bonect/binary_plan_resource_tuning_hybenchxp_report_$(date +%Y%m%d_%H%M%S).md"
}

case "${CMD}" in
  setup)
    "${ROOT_DIR}/shell/setup_binary_plan_tuning_hybenchxp_workers.sh"
    ;;
  screen|endpoint|endpoint-screening)
    run_tuner endpoint-screening
    ;;
  baseline|probe-baseline)
    "${PYTHON_BIN}" "${ROOT_DIR}/shell/probe_hybenchxp_baseline.py" \
      --config "${CONFIG}" \
      --output-dir "${RUN_DIR}" \
      --worker "${BINARY_PLAN_TUNING_HYBENCHXP_BASELINE_WORKER:-cpu_mem_w1}" \
      --label "${BINARY_PLAN_TUNING_HYBENCHXP_BASELINE_LABEL:-baseline_current_config}"
    ;;
  cpu)
    run_stage cpu
    ;;
  memory|mem)
    run_stage memory
    ;;
  io)
    rerun_memory_before_io_if_requested
    run_stage io
    ;;
  none|no-bottleneck|no_bottleneck)
    run_stage none
    ;;
  report)
    run_report
    ;;
  all)
    run_tuner endpoint-screening
    run_stage cpu
    run_stage memory
    run_stage none
    run_stage io
    run_report
    ;;
  *)
    echo "usage: $0 {setup|screen|baseline|cpu|memory|io|none|report|all}" >&2
    exit 2
    ;;
esac

echo "binary_plan_tuning_hybenchxp run_dir=${RUN_DIR}"
