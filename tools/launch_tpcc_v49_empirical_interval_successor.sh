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
package=$project/history/tpcc_v49_empirical_interval_successor_package_20260804
runtime=$project/history/tpcc_v40_clean5_exact_recalibration_package_20260803/runtime
formal=$project/history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json
manifest=$package/manifest.json
selection=$package/top5.json
expected_formal=62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0

[[ -f "$manifest" && -f "$selection" && -f "$formal" ]]
selection_sha=$(sha256sum "$selection" | awk '{print $1}')
manifest_selection_sha=$(jq -r '.files["top5.json"]' "$manifest")
formal_sha=$(sha256sum "$formal" | awk '{print $1}')
[[ "$selection_sha" == "$manifest_selection_sha" ]]
[[ "$formal_sha" == "$expected_formal" ]]
[[ "$(jq -r '.contract' "$manifest")" == "tpcc_v49_zero_call_conditional_handoff_v1" ]]
[[ "$(jq -r '.workload_calls_added' "$manifest")" == "0" ]]
[[ "$(jq -r '.formal_state_mutated' "$manifest")" == "false" ]]

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$runtime:$project/tools:$project"
args=(
  --selection "$selection"
  --campaign-root "$package"
  --expected-formal-state-sha256 "$expected_formal"
)
if [[ "$mode" == "verify-only" ]]; then
  args+=(--verify-only)
fi
exec /home/anonymous/miniconda3/envs/resource_adaptive/bin/python -u \
  "$project/tools/run_tpcc_v49_autonomous_empirical_interval_loop.py" \
  "${args[@]}"
