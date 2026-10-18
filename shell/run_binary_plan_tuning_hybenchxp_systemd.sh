#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMD="${1:-all}"
RUN_DIR="${BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR:-/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/$(date +%Y%m%d_%H%M%S)}"
UNIT_SUFFIX="$(date +%Y%m%d_%H%M%S)_${CMD}"
UNIT_NAME="binary_plan_tuning_hybenchxp_${UNIT_SUFFIX}"

mkdir -p "${RUN_DIR}"

export BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR="${RUN_DIR}"
export BINARY_PLAN_TUNING_HYBENCHXP_CONFIG="${BINARY_PLAN_TUNING_HYBENCHXP_CONFIG:-${ROOT_DIR}/config/binary_plan_tuning_hybenchxp.json}"

DRIVER_LOG="${RUN_DIR}/${CMD}_stage_driver.log"
UNIT_FILE="${RUN_DIR}/${CMD}_stage_systemd_unit.txt"
STATUS_FILE="${RUN_DIR}/${CMD}_stage_systemd_status.log"

systemd-run --user --unit="${UNIT_NAME}" --collect \
  --property=WorkingDirectory="${ROOT_DIR}" \
  --setenv=BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR="${BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR}" \
  --setenv=BINARY_PLAN_TUNING_HYBENCHXP_CONFIG="${BINARY_PLAN_TUNING_HYBENCHXP_CONFIG}" \
  --setenv=PYTHON="${PYTHON:-python3}" \
  /bin/bash -lc "exec '${ROOT_DIR}/shell/run_binary_plan_tuning_hybenchxp.sh' '${CMD}' > '${DRIVER_LOG}' 2>&1"

printf '%s.service\n' "${UNIT_NAME}" > "${UNIT_FILE}"
systemctl --user status "${UNIT_NAME}.service" --no-pager > "${STATUS_FILE}" 2>&1 || true

echo "binary_plan_tuning_hybenchxp systemd_unit=${UNIT_NAME}.service"
echo "binary_plan_tuning_hybenchxp run_dir=${RUN_DIR}"
echo "binary_plan_tuning_hybenchxp driver_log=${DRIVER_LOG}"
