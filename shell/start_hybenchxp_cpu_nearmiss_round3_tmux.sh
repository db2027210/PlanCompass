#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="${1:-hyxp_cpu_resume_nearmiss_20260709}"
RUN_DIR="${2:-/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/hyxp_cpu_formal_20260709_131733}"
CONFIG="${BINARY_PLAN_TUNING_HYBENCHXP_CONFIG:-${ROOT_DIR}/config/binary_plan_tuning_hybenchxp_cpu_nearmiss_resume_20260709.json}"
PYTHON_BIN="${PYTHON:-/home/anonymous/miniconda3/envs/resource_adaptive/bin/python}"
JSONL="${RUN_DIR}/cpu_binary_search_hybenchxp.jsonl"

if tmux has-session -t "${SESSION}" 2>/dev/null; then
  echo "tmux session already exists: ${SESSION}" >&2
  exit 1
fi

"${PYTHON_BIN}" "${ROOT_DIR}/shell/verify_hybenchxp_cpu_nearmiss_anchor.py" \
  --config "${CONFIG}" \
  --jsonl "${JSONL}" \
  --expect-next-round 3 \
  --expect-resume-id cpu_round2_nearmiss_c026_continue_20260709 \
  --expect-plan-probe-workers 4 \
  --expect-run-plan-probe-for-full-bruteforce true

tmux new-session -d -s "${SESSION}" \
  "cd '${ROOT_DIR}' && BINARY_PLAN_TUNING_HYBENCHXP_CONFIG='${CONFIG}' PYTHON='${PYTHON_BIN}' '${ROOT_DIR}/shell/resume_hybenchxp_cpu_nearmiss_round3.sh' '${RUN_DIR}'"

echo "started ${SESSION}"
echo "attach: tmux attach -t ${SESSION}"
echo "log: ${JSONL}"
