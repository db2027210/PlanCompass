#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 verify-only|armed" >&2
  exit 64
fi
mode=$1
case "$mode" in
  verify-only|armed) ;;
  *) echo "invalid mode: $mode" >&2; exit 64 ;;
esac

project=/home/anonymous/program/resource_adaptive
package=$project/history/tpcc_v48_clean5_failed_gate_successor_package_20260804
runtime=$project/history/tpcc_v40_clean5_exact_recalibration_package_20260803/runtime
formal=$project/history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json

"$project/tools/apply_tpcc_v48_clean5_successor.sh" >/dev/null
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$runtime:$project/tools:$project"

args=(
  --selection "$package/top5.json"
  --campaign-root "$package"
  --expected-formal-state-sha256 62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0
)
if [[ "$mode" == "verify-only" ]]; then
  args+=(--verify-only)
fi
exec /home/anonymous/miniconda3/envs/resource_adaptive/bin/python -u \
  "$project/tools/run_tpcc_v48_autonomous_clean5_successor_loop.py" \
  "${args[@]}"
