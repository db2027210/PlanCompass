#!/usr/bin/env bash
set -euo pipefail

project_root="/home/anonymous/program/resource_adaptive"
python_bin="/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
manifest="/tmp/tpcc_final_fresh0_autonomous_v50_cross_run_stability_v31_20260803/manifest.json"
manifest_sha="09e0437e58646b7b28dc93caf05dcfbf6917c2734607a35dba8a9271bfa992b6"

cd "$project_root"
"$python_bin" tools/preflight_tpcc_v31_cross_run_stability.py \
  "$manifest" --manifest-sha256 "$manifest_sha"

if pgrep -f '[t]pcc_autonomous_target10_controller_v30_score_stability.py' >/dev/null; then
  echo "TPC-C v30 controller is still active" >&2
  exit 75
fi
if pgrep -f '[t]pcc_autonomous_target10_controller_v31_cross_run_stability.py' >/dev/null; then
  echo "TPC-C v31 controller is already active" >&2
  exit 75
fi
if pgrep -f '[r]un_tpcc_round11_calibrated_top5_strict_serial.py.*autonomous_' >/dev/null; then
  echo "TPC-C real replay runner is active" >&2
  exit 75
fi

echo "manifest_sha256=$manifest_sha"
echo "controller_mode=foreground_exec_requires_host_process_supervisor"
exec "$python_bin" -u tools/tpcc_autonomous_target10_controller_v31_cross_run_stability.py \
  "$manifest" --armed --manifest-sha256 "$manifest_sha"
