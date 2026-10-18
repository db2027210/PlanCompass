#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMD="${1:-all}"
RUN_DIR="${BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR:-/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/$(date +%Y%m%d_%H%M%S)}"
SESSION="${BINARY_PLAN_TUNING_HYBENCHXP_TMUX_SESSION:-binary_plan_tuning_hybenchxp_$(date +%Y%m%d_%H%M%S)_${CMD}}"

mkdir -p "${RUN_DIR}"

DRIVER_LOG="${RUN_DIR}/${CMD}_stage_driver.log"
SESSION_FILE="${RUN_DIR}/${CMD}_stage_tmux_session.txt"
EXIT_FILE="${RUN_DIR}/${CMD}_stage_tmux_exit.json"

if tmux has-session -t "${SESSION}" 2>/dev/null; then
  echo "tmux session already exists: ${SESSION}" >&2
  exit 1
fi

tmux new-session -d -s "${SESSION}" \
  "cd '${ROOT_DIR}' && printf '{\"status\":\"running\",\"started_at\":\"%s\"}\n' \"\$(date '+%Y-%m-%d %H:%M:%S')\" > '${EXIT_FILE}' && BINARY_PLAN_TUNING_HYBENCHXP_RUN_DIR='${RUN_DIR}' BINARY_PLAN_TUNING_HYBENCHXP_CONFIG='${BINARY_PLAN_TUNING_HYBENCHXP_CONFIG:-${ROOT_DIR}/config/binary_plan_tuning_hybenchxp.json}' PYTHON='${PYTHON:-python3}' PYTHONUNBUFFERED=1 setsid bash '${ROOT_DIR}/shell/run_binary_plan_tuning_hybenchxp.sh' '${CMD}' > '${DRIVER_LOG}' 2>&1; rc=\$?; printf '{\"status\":\"exited\",\"exit_code\":%s,\"ended_at\":\"%s\"}\n' \"\$rc\" \"\$(date '+%Y-%m-%d %H:%M:%S')\" > '${EXIT_FILE}'; exit \$rc"

printf '%s\n' "${SESSION}" > "${SESSION_FILE}"

echo "binary_plan_tuning_hybenchxp tmux_session=${SESSION}"
echo "binary_plan_tuning_hybenchxp run_dir=${RUN_DIR}"
echo "binary_plan_tuning_hybenchxp driver_log=${DRIVER_LOG}"
