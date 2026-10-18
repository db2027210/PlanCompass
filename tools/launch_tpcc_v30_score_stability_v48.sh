#!/usr/bin/env bash
set -euo pipefail

project_root="/home/anonymous/program/resource_adaptive"
python_bin="/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
manifest="/tmp/tpcc_final_fresh0_autonomous_v48_score_stability_20260803/manifest.json"
manifest_sha="40d075a4d6f0fd3e1116d8ceb4579c41114d7c98bf0436210ca12e553996b190"
controller_log="$project_root/history/tpcc_final_algorithm_fresh10_v29_20260731T2005/autonomous_controller_v32/controller_v30.log"

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

nohup "$python_bin" -u tools/tpcc_autonomous_target10_controller_v30_score_stability.py \
  "$manifest" --armed --manifest-sha256 "$manifest_sha" \
  >"$controller_log" 2>&1 &
controller_pid="$!"
sleep 2
kill -0 "$controller_pid"
echo "controller_pid=$controller_pid"
echo "controller_log=$controller_log"
echo "manifest_sha256=$manifest_sha"
