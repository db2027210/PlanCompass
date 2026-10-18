#!/usr/bin/env bash
set -euo pipefail

# Prepared only. Run after the controller confirms the disk clone has finished.
root=/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/hyxp_memory_fair_v90_feedback_binary_cap240_20260715/round_002
config=config/binary_plan_tuning_hybenchxp_memory_fair_v89_cap240_case1_20260715.json

python3 -u binary_plan_resource_tuner.py \
  --config "$config" \
  --output-dir "$root/baseline" \
  binary-search --target memory --baseline-only

python3 -u binary_plan_resource_tuner.py \
  --config "$config" \
  --output-dir "$root/candidate" \
  validate-params --target memory \
  --worker-name cpu_mem_w1 \
  --label memory_v90_r02_ctc7p5025 \
  --baseline-output-dir "$root/baseline" \
  --params-json '{"seq_page_cost":1.0,"random_page_cost":4.0,"cpu_tuple_cost":7.5025,"cpu_index_tuple_cost":0.005,"cpu_operator_cost":0.0025,"parallel_setup_cost":1000.0,"parallel_tuple_cost":0.1}'
