#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive_mysql
HISTORY="$PROJECT/history/mysql_four_scene_20260809"
RUN_ID=v1169_to_b_tpch_outer3_timeout_resume_20260830_tpch
OUTPUT="$HISTORY/$RUN_ID"
SOURCE="$HISTORY/v1167_to_b_tpch_pg_rejection_fresh_rerun_20260830_tpch"
PYTHON=/home/anonymous/miniconda3/envs/py313/bin/python
export MYSQL_TWO_WORKER_AUTHORITY_ROOT="$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830"

if [[ -e "$OUTPUT" || -L "$OUTPUT" ]]; then
  echo "fresh Algorithm B continuation output already exists: $OUTPUT" >&2
  exit 3
fi

cd "$PROJECT"
exec "$PYTHON" scripts/run_mysql_tpch_algorithm_b_outer3_timeout_resume_v1.py \
  --run-id "$RUN_ID" \
  --resume-from "$SOURCE" \
  --scene tpch_sf4_memory
