#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${1:-$(cat /tmp/hybenchxp_current_run_dir)}"
CONFIG="${BINARY_PLAN_TUNING_HYBENCHXP_CONFIG:-${ROOT_DIR}/config/binary_plan_tuning_hybenchxp.json}"
PYTHON_BIN="${PYTHON:-/home/anonymous/miniconda3/envs/resource_adaptive/bin/python}"

mkdir -p "${RUN_DIR}"
{
  echo
  echo "===== CPU resume start $(date '+%F %T %Z') ====="
  echo "run_dir=${RUN_DIR}"
  echo "config=${CONFIG}"
  echo "python=${PYTHON_BIN}"
} >> "${RUN_DIR}/driver_all.log"

exec >> "${RUN_DIR}/driver_all.log" 2>&1
exec "${PYTHON_BIN}" "${ROOT_DIR}/binary_plan_resource_tuner.py" \
  --config "${CONFIG}" \
  --output-dir "${RUN_DIR}" \
  binary-search \
  --target cpu \
  --resume
