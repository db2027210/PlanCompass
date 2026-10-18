#!/usr/bin/env bash
set -euo pipefail

ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726/job_final_algorithm_fresh10_v36_20260731T2020/autonomous_target10_v57_fresh0
SESSION=job_v62_target10
STAMP=$(date +%Y%m%dT%H%M%S)
LOG=${ROOT}/controller_v62_${STAMP}.log

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha /tmp/job_fresh10_target10_autonomous_v62.py 748ba44a28c446a8510ca1463750b1db3c7a9674f46cd9d4d0876c3c2c021a2a
require_sha /tmp/launch_job_target10_autonomous_v62.sh f61235c0a838d5afc3237cbc3bf83d278b95c6b3c32f4bb920298dd00fb12a33
require_sha /tmp/job_fresh10_target10_autonomous_v61.py 49e6c210380c0852d9b0dffd7da597d318de1932994351c31b028cc6cbf53053
require_sha /tmp/job_fresh10_target10_autonomous_v60.py 394f207eea62d93ceae570d3bca25abe36c4ae787a37f8913cbbc4e492ed222b
require_sha /tmp/job_v62_deployment_manifest.json 6d0ce1431880fb9607167024fc36baf612db24d3a69c33f6857ef0d852669265

chmod +x /tmp/launch_job_target10_autonomous_v62.sh
timeout 180 env V62_PREFLIGHT_ONLY=1 /tmp/launch_job_target10_autonomous_v62.sh

if tmux has-session -t "${SESSION}" 2>/dev/null; then
  echo "tmux session already exists: ${SESSION}" >&2
  exit 75
fi

tmux new-session -d -s "${SESSION}" \
  env V62_PREFLIGHT_ONLY=0 bash -lc "exec /tmp/launch_job_target10_autonomous_v62.sh >> '${LOG}' 2>&1"
sleep 2
tmux has-session -t "${SESSION}"
test -f "${LOG}"

echo "JOB_V62_SESSION=${SESSION}"
echo "JOB_V62_LOG=${LOG}"
echo "JOB_V62_PANE_PID=$(tmux display-message -p -t "${SESSION}" '#{pane_pid}')"
