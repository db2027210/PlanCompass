#!/usr/bin/env bash
set -euo pipefail

ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726/job_final_algorithm_fresh10_v36_20260731T2020/autonomous_target10_v57_fresh0
SESSION=job_v63_target10
STAMP=$(date +%Y%m%dT%H%M%S)
LOG=${ROOT}/controller_v63_${STAMP}.log

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha /tmp/job_fresh10_target10_autonomous_v63.py 15b7e1658d3328a3a92d2648ed610cf9153ec2e26e848b46fc2e756a30a74980
require_sha /tmp/launch_job_target10_autonomous_v63.sh c70a5441a0959b9c672862791d38ce72c01cf6ca7db4b5454444b5f76f4ccf4d
require_sha /tmp/job_fresh10_target10_autonomous_v62.py 748ba44a28c446a8510ca1463750b1db3c7a9674f46cd9d4d0876c3c2c021a2a
require_sha /tmp/job_fresh10_target10_autonomous_v61.py 49e6c210380c0852d9b0dffd7da597d318de1932994351c31b028cc6cbf53053
require_sha /tmp/job_fresh10_target10_autonomous_v60.py 394f207eea62d93ceae570d3bca25abe36c4ae787a37f8913cbbc4e492ed222b
require_sha /tmp/job_v63_deployment_manifest.json 300ecf59e88fed15d5d5db511cf45613c4bd88eec34a528b3ff4dea9c1170bf7

chmod +x /tmp/launch_job_target10_autonomous_v63.sh
timeout 180 env V63_PREFLIGHT_ONLY=1 /tmp/launch_job_target10_autonomous_v63.sh

if tmux has-session -t "${SESSION}" 2>/dev/null; then
  echo "tmux session already exists: ${SESSION}" >&2
  exit 75
fi

tmux new-session -d -s "${SESSION}" \
  env V63_PREFLIGHT_ONLY=0 bash -lc "exec /tmp/launch_job_target10_autonomous_v63.sh >> '${LOG}' 2>&1"
sleep 2
tmux has-session -t "${SESSION}"
test -f "${LOG}"

echo "JOB_V63_SESSION=${SESSION}"
echo "JOB_V63_LOG=${LOG}"
echo "JOB_V63_PANE_PID=$(tmux display-message -p -t "${SESSION}" '#{pane_pid}')"
