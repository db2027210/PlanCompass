#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive_mysql
HISTORY="$PROJECT/history/mysql_four_scene_20260809"
B_STATE="$HISTORY/v1132_to_b_tpch_client_qps_frozen_outer2_resume_20260830_tpch/run_state.json"
C_RUN_ID=v1133_to_c_tpch_client_qps_timeout_rows_20260830_tpch
C_OUTPUT="$HISTORY/$C_RUN_ID"
C_BARRIER="$HISTORY/v1133_tpch_c_single_scene_start_barrier_20260830"
PYTHON=/home/anonymous/miniconda3/envs/py313/bin/python
export MYSQL_TWO_WORKER_AUTHORITY_ROOT="$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830"

while true; do
  status=$(jq -r '.status // "MISSING"' "$B_STATE")
  case "$status" in
    COMPLETE)
      break
      ;;
    FAILED_CLOSED|NON_CONVERGED)
      echo "Algorithm B ended without convergence: $status" >&2
      exit 2
      ;;
    *)
      sleep 20
      ;;
  esac
done

if [[ -e "$C_OUTPUT" || -L "$C_OUTPUT" ]]; then
  echo "fresh Algorithm C output already exists: $C_OUTPUT" >&2
  exit 3
fi

cd "$PROJECT"
"$PYTHON" scripts/run_mysql_tpch_algorithm_c_client_qps_normalized_v3.py \
  --run-id "$C_RUN_ID" \
  --checkpoint "$HISTORY/v1118_tpch_c_disk2_checkpoint_rebind_20260830/prepared_binding.json" \
  --runtime-preflight "$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/transition_preflight.json" \
  --prepared-binding "$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/prepared_binding.json" \
  --benchmark-accounts "$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/benchmark_accounts_binding.json" \
  --fresh-r0 "$HISTORY/v1119_tpch_c_disk2_fresh_r0_binding_20260830/fresh_r0_binding.json" \
  --replay-quarantine "$HISTORY/v1120_tpch_c_disk2_empty_quarantine_20260830/replay_quarantine.json" \
  --fresh-search \
  --scene tpch_sf4_memory \
  --scene-start-barrier "$C_BARRIER"

exec "$PYTHON" scripts/compare_mysql_tpch_abc_client_qps_after_bc_v3.py \
  --run-id v1134_tpch_abc_client_qps_counterbalanced_20260830 \
  --output-dir "$HISTORY/v1134_tpch_abc_client_qps_counterbalanced_20260830"
