#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive_mysql
HISTORY="$PROJECT/history/mysql_four_scene_20260809"
C_RUN_ID=v1162_to_c_tpch_semantic_batch_resume_outer14_20260830_tpch
C_OUTPUT="$HISTORY/$C_RUN_ID"
C_CHECKPOINT="$HISTORY/v1159_tpch_c_semantic_batch_correction_outer13_20260830/prepared_binding.json"
C_QUARANTINE="$HISTORY/v1161_tpch_c_semantic_batch_empty_quarantine_outer13_20260830/replay_quarantine.json"
C_BARRIER="$HISTORY/v1162_tpch_c_single_scene_start_barrier_20260830"
PYTHON=/home/anonymous/miniconda3/envs/py313/bin/python
export MYSQL_TWO_WORKER_AUTHORITY_ROOT="$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830"

if [[ -e "$C_OUTPUT" || -L "$C_OUTPUT" ]]; then
  echo "fresh Algorithm C output already exists: $C_OUTPUT" >&2
  exit 3
fi

cd "$PROJECT"
exec "$PYTHON" scripts/run_mysql_tpch_algorithm_c_client_qps_normalized_v6.py \
  --run-id "$C_RUN_ID" \
  --checkpoint "$C_CHECKPOINT" \
  --runtime-preflight "$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/transition_preflight.json" \
  --prepared-binding "$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/prepared_binding.json" \
  --benchmark-accounts "$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/benchmark_accounts_binding.json" \
  --fresh-r0 "$HISTORY/v1119_tpch_c_disk2_fresh_r0_binding_20260830/fresh_r0_binding.json" \
  --replay-quarantine "$C_QUARANTINE" \
  --scene tpch_sf4_memory \
  --scene-start-barrier "$C_BARRIER"
