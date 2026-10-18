#!/usr/bin/env bash
set -euo pipefail

project_root="/home/anonymous/program/resource_adaptive"
python_bin="/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
manifest="/tmp/tpcc_final_fresh0_autonomous_v51_validation_repeat_v32_20260803/manifest.json"
manifest_sha="b05eb3a04999376b92d936bba26159a18b47a51a0d6c988127952bf873f5251f"

cd "$project_root"
"$python_bin" tools/preflight_tpcc_v32_validation_repeat.py \
  "$manifest" --manifest-sha256 "$manifest_sha"

if pgrep -f '[t]pcc_autonomous_target10_controller_v30_score_stability.py' >/dev/null; then
  echo "TPC-C v30 controller is still active" >&2
  exit 75
fi
if pgrep -f '[t]pcc_autonomous_target10_controller_v31_cross_run_stability.py' >/dev/null; then
  echo "TPC-C v31 controller is still active" >&2
  exit 75
fi
if pgrep -f '[t]pcc_autonomous_target10_controller_v32_validation_repeat.py' >/dev/null; then
  echo "TPC-C v32 controller is already active" >&2
  exit 75
fi
if pgrep -f '[r]un_tpcc_round11_calibrated_top5_strict_serial.py.*autonomous_' >/dev/null; then
  echo "TPC-C real replay runner is active" >&2
  exit 75
fi

echo "manifest_sha256=$manifest_sha"
echo "controller_mode=foreground_exec_requires_host_process_supervisor"
exec "$python_bin" -u tools/tpcc_autonomous_target10_controller_v32_validation_repeat.py \
  "$manifest" --armed --manifest-sha256 "$manifest_sha"
