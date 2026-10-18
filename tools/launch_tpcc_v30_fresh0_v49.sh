#!/usr/bin/env bash
set -euo pipefail

project_root="/home/anonymous/program/resource_adaptive"
python_bin="/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
manifest="/tmp/tpcc_final_fresh0_autonomous_v49_score_stability_fresh0_20260803/manifest.json"
manifest_sha="9bc07b4a35aa3135c33e794bedf5e9e591f450506db273d6abba4ae47397c942"
controller_root="$project_root/history/tpcc_final_algorithm_fresh0_v30_20260803/autonomous_controller"

cd "$project_root"
"$python_bin" tools/preflight_tpcc_v30_score_stability.py \
  "$manifest" --manifest-sha256 "$manifest_sha"

if pgrep -f '[t]pcc_autonomous_target10_controller_v29_prelaunch_fingerprint.py' >/dev/null; then
  echo "old v29 controller is still active" >&2
  exit 75
fi
if pgrep -f '[t]pcc_autonomous_target10_controller_v30_score_stability.py' >/dev/null; then
  echo "v30 controller is already active" >&2
  exit 75
fi
if pgrep -f '[r]un_tpcc_round11_calibrated_top5_strict_serial.py.*autonomous_' >/dev/null; then
  echo "TPC-C real replay runner is active" >&2
  exit 75
fi

echo "manifest_sha256=$manifest_sha"
echo "controller_mode=foreground_exec_requires_host_process_supervisor"
exec "$python_bin" -u tools/tpcc_autonomous_target10_controller_v30_score_stability.py \
  "$manifest" --armed --manifest-sha256 "$manifest_sha"
