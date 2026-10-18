#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive_mysql
HISTORY="$PROJECT/history/mysql_four_scene_20260809"
RUN_ID=v1168_tpch_abc_pg_final_same_batch_20260830
OUTPUT="$HISTORY/$RUN_ID"
PYTHON=/home/anonymous/miniconda3/envs/py313/bin/python
export MYSQL_TWO_WORKER_AUTHORITY_ROOT="$HISTORY/v1115_tpch_bc_disk2_two_worker_live_authority_20260830"

if [[ -e "$OUTPUT" || -L "$OUTPUT" ]]; then
  echo "fresh A/B/C replay output already exists: $OUTPUT" >&2
  exit 3
fi

cd "$PROJECT"
exec "$PYTHON" scripts/replay_mysql_tpch_bc_pg_final_vs_a_v2.py \
  --run-id "$RUN_ID" \
  --output-dir "$OUTPUT"
