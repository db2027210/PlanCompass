#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

cd "$SCRIPT_DIR"

# Two-pass fixed profile:
# 1) resource run: low
# 2) plan run: shadow (auto_explain path in experiment_run.sh)
# - series M
# - TPCC only
# - container node1 only
# - recreate container for each case
# - use disk2-based compose env

RESOURCE_EXP_TYPE="${RESOURCE_EXP_TYPE:-low}"
PLAN_EXP_TYPE="${PLAN_EXP_TYPE:-shadow}"

common_args=(
  --series M
  --bench tpcc
  --containers 1
  --compose-dir /home/anonymous/dockercompose/og
  --compose-file og_node_docker_compose.yml
  --compose-env-file .env.M.disk2
)

echo "=== Pass 1/2: resource run (${RESOURCE_EXP_TYPE}) ==="
./run_series_cases.sh "$RESOURCE_EXP_TYPE" "${common_args[@]}" "$@"

echo "=== Pass 2/2: plan run (${PLAN_EXP_TYPE}) ==="
./run_series_cases.sh "$PLAN_EXP_TYPE" "${common_args[@]}" --exec-only "$@"
