#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive_mysql
HISTORY="$PROJECT/history/mysql_four_scene_20260809"
C_RUN_ID=v1137_to_c_tpch_cached_no_move_resume_20260830_tpch
C_OUTPUT="$HISTORY/$C_RUN_ID"
C_CHECKPOINT="$HISTORY/v1149_tpch_c_cached_no_move_boundary_outer9_final_20260830/prepared_binding.json"
C_QUARANTINE="$HISTORY/v1150_tpch_c_cached_no_move_empty_quarantine_outer9_final_20260830/replay_quarantine.json"
C_BARRIER="$HISTORY/v1137_tpch_c_single_scene_start_barrier_20260830"
PYTHON=/home/anonymous/miniconda3/envs/py313/bin/python
export MYSQL_TWO_WORKER_AUTHORITY_ROOT="$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830"

if [[ -e "$C_OUTPUT" || -L "$C_OUTPUT" ]]; then
  echo "fresh Algorithm C output already exists: $C_OUTPUT" >&2
  exit 3
fi

cd "$PROJECT"
"$PYTHON" scripts/run_mysql_tpch_algorithm_c_client_qps_normalized_v5.py \
  --run-id "$C_RUN_ID" \
  --checkpoint "$C_CHECKPOINT" \
  --runtime-preflight "$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/transition_preflight.json" \
  --prepared-binding "$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/prepared_binding.json" \
  --benchmark-accounts "$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/benchmark_accounts_binding.json" \
  --fresh-r0 "$HISTORY/v1119_tpch_c_disk2_fresh_r0_binding_20260830/fresh_r0_binding.json" \
  --replay-quarantine "$C_QUARANTINE" \
  --scene tpch_sf4_memory \
  --scene-start-barrier "$C_BARRIER"

exec "$PYTHON" scripts/compare_mysql_tpch_abc_client_qps_after_bc_v6.py \
  --run-id v1139_tpch_abc_client_qps_counterbalanced_cached_no_move_20260830 \
  --output-dir "$HISTORY/v1139_tpch_abc_client_qps_counterbalanced_cached_no_move_20260830"
