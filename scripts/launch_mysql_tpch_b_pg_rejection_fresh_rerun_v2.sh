#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive_mysql
HISTORY="$PROJECT/history/mysql_four_scene_20260809"
B_RUN_ID=v1167_to_b_tpch_pg_rejection_fresh_rerun_20260830_tpch
B_OUTPUT="$HISTORY/$B_RUN_ID"
B_SOURCE="$HISTORY/v1117_to_b_tpch_client_qps_disk2_fresh_20260830_tpch"
PYTHON=/home/anonymous/miniconda3/envs/py313/bin/python
export MYSQL_TWO_WORKER_AUTHORITY_ROOT="$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830"

if [[ -e "$B_OUTPUT" || -L "$B_OUTPUT" ]]; then
  echo "fresh Algorithm B output already exists: $B_OUTPUT" >&2
  exit 3
fi

cd "$PROJECT"
exec "$PYTHON" scripts/run_mysql_tpch_algorithm_b_client_qps_fresh_rerun_v5.py \
  --run-id "$B_RUN_ID" \
  --resume-from "$B_SOURCE" \
  --scene tpch_sf4_memory
