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
  "${PYTHON_BIN}" - "$@" <<'PY'
import runpy
import sys

sys.argv = ["hyxp_cost_tuner", *sys.argv[1:]]
runpy.run_module("binary_plan_resource_tuner", run_name="__main__")
PY
}

run_stage() {
  local target="$1"
  shift || true
  local args=(--config "${CONFIG}" --output-dir "${RUN_DIR}" binary-search --target "${target}")
  if [[ -n "${BINARY_PLAN_TUNING_HYBENCHXP_MAX_ROUNDS:-}" ]]; then
    args+=(--max-rounds "${BINARY_PLAN_TUNING_HYBENCHXP_MAX_ROUNDS}")
  fi
  if [[ -n "${BINARY_PLAN_TUNING_HYBENCHXP_MAX_CANDIDATES:-}" ]]; then
    args+=(--max-candidates "${BINARY_PLAN_TUNING_HYBENCHXP_MAX_CANDIDATES}")
  fi
  run_tuner "${args[@]}" "$@"
}

run_report() {
  run_tuner \
    --config "${CONFIG}" \
    --output-dir "${RUN_DIR}" \
    report \
    --repo-report "report/bonect/binary_plan_resource_tuning_hybenchxp_report_$(date +%Y%m%d_%H%M%S).md"
}

case "${CMD}" in
  screen|endpoint|endpoint-screening)
    run_tuner --config "${CONFIG}" --output-dir "${RUN_DIR}" endpoint-screening
    ;;
  cpu)
    run_stage cpu
    ;;
  memory|mem)
    run_stage memory
    ;;
  io)
    run_stage io
    ;;
  none|no-bottleneck|no_bottleneck)
    run_stage none
    ;;
  report)
    run_report
    ;;
  all)
    run_tuner --config "${CONFIG}" --output-dir "${RUN_DIR}" endpoint-screening
    run_stage cpu
    run_stage memory
    run_stage none
    run_stage io
    run_report
    ;;
  *)
    echo "usage: $0 {screen|cpu|memory|io|none|report|all}" >&2
    exit 2
    ;;
esac

echo "hyxp_cost_tuning run_dir=${RUN_DIR}"
