#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive_mysql
HISTORY="$PROJECT/history/mysql_four_scene_20260809"
RUN_ID=v1170_to_c_tpch_current_baseline_fresh_20260830_tpch
OUTPUT="$HISTORY/$RUN_ID"
BARRIER="$HISTORY/v1170_tpch_c_current_baseline_start_barrier_20260830"
PYTHON=/home/anonymous/miniconda3/envs/py313/bin/python
export MYSQL_TWO_WORKER_AUTHORITY_ROOT="$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830"

if [[ -e "$OUTPUT" || -L "$OUTPUT" ]]; then
  echo "fresh Algorithm C output already exists: $OUTPUT" >&2
  exit 3
fi

cd "$PROJECT"
exec "$PYTHON" scripts/run_mysql_tpch_algorithm_c_client_qps_normalized_v10.py \
  --run-id "$RUN_ID" \
  --checkpoint "$HISTORY/v1118_tpch_c_disk2_checkpoint_rebind_20260830/prepared_binding.json" \
  --runtime-preflight "$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/transition_preflight.json" \
  --prepared-binding "$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/prepared_binding.json" \
  --benchmark-accounts "$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830/benchmark_accounts_binding.json" \
  --fresh-r0 "$HISTORY/v1119_tpch_c_disk2_fresh_r0_binding_20260830/fresh_r0_binding.json" \
  --replay-quarantine "$HISTORY/v1120_tpch_c_disk2_empty_quarantine_20260830/replay_quarantine.json" \
  --fresh-search \
  --scene tpch_sf4_memory \
  --scene-start-barrier "$BARRIER"
