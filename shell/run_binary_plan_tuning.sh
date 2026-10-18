#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${BINARY_PLAN_TUNING_CONFIG:-${ROOT_DIR}/config/binary_plan_tuning_example.json}"
RUN_DIR="${BINARY_PLAN_TUNING_RUN_DIR:-/disk2/resource_adaptive/binary_plan_tuning/runs/$(date +%Y%m%d_%H%M%S)}"
PYTHON_BIN="${PYTHON:-python3}"
CMD="${1:-all}"

run_tuner() {
  "${PYTHON_BIN}" "${ROOT_DIR}/binary_plan_resource_tuner.py" \
    --config "${CONFIG}" \
    --output-dir "${RUN_DIR}" \
    "$@"
}

run_all() {
  run_tuner endpoint-screening
  run_tuner binary-search --target cpu
  run_tuner binary-search --target memory
  run_tuner binary-search --target io
  run_tuner report --repo-report "report/bonect/binary_plan_resource_tuning_report_$(date +%Y%m%d_%H%M%S).md"
}

case "${CMD}" in
  setup)
    "${ROOT_DIR}/shell/setup_binary_plan_tuning_workers.sh"
    ;;
  setup-single)
    "${ROOT_DIR}/shell/setup_binary_plan_tuning_docker.sh"
    ;;
  screen|endpoint|endpoint-screening)
    run_tuner endpoint-screening
    ;;
  cpu)
    run_tuner binary-search --target cpu
    ;;
  memory|mem)
    run_tuner binary-search --target memory
    ;;
  io)
    run_tuner binary-search --target io
    ;;
  report)
    run_tuner report --repo-report "report/bonect/binary_plan_resource_tuning_report_$(date +%Y%m%d_%H%M%S).md"
    ;;
  all|full128|all-full128)
    CONFIG="${BINARY_PLAN_TUNING_CONFIG:-${ROOT_DIR}/config/binary_plan_tuning_example.json}"
    run_all
    ;;
  budget|all-budget|smoke)
    CONFIG="${BINARY_PLAN_TUNING_CONFIG:-${ROOT_DIR}/config/binary_plan_tuning_budget_example.json}"
    run_all
    ;;
  *)
    echo "usage: $0 {setup|setup-single|screen|cpu|memory|io|report|all|full128|budget}" >&2
    exit 2
    ;;
esac

echo "binary_plan_tuning run_dir=${RUN_DIR}"
