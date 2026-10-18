#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/anonymous/program/resource_adaptive
PYTHON=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
RUN_ID=${RUN_ID:-pg_bonect_phase2_vm_estimator_fix_v84_cpu_20260711_222545}
RUN_ARGS=(--run)
if [[ ${DRY_RUN:-0} == 1 ]]; then
  RUN_ARGS=()
fi

cd "$ROOT"
exec "$PYTHON" shell/run_pg_benchmark_bottleneck_matrix.py \
  --scenario-set bonect \
  --bonect-bottlenecks cpu \
  --run-id "$RUN_ID" \
  --generated-config-dir config/generated/pg_benchmark_bottleneck_matrix_local \
  --python-executable "$PYTHON" \
  --use-resource-estimator \
  --apply-vm-profile \
  --no-warmup \
  --no-adaptive-bottleneck-calibration \
  --hybench-run-mins 5 \
  --hybench-tp-clients 60 \
  --hybench-ap-clients 8 \
  --hybench-ap-query-ids 6,8,10,11 \
  --hybench-ap-query-weights 15,35,35,15 \
  --hybench-ap-query-sequence 8,10,6,8,10,11,8,10,6,8,10,11 \
  --hybench-progress-windows 10 \
  --hybench-statement-timeout-ms 420000 \
  --validation-warmup-timeout-sec 60 \
  --runtime-monitor-warmup-sec 30 \
  --runtime-monitor-interval-sec 1 \
  --vm-runtime-memory-mode vm_used \
  --measured-workload-timeout-sec 480 \
  --vm-cpu-cpu 2 \
  --vm-cpu-mem 8G \
  --vm-cpu-read-mb 800 \
  --vm-cpu-write-mb 800 \
  --vm-disk-dev vda \
  --bonect-capability cpu=2 \
  --bonect-capability memory=8 \
  --bonect-capability io=800 \
  --bonect-active-params seq_page_cost,random_page_cost,cpu_tuple_cost,cpu_index_tuple_cost,cpu_operator_cost,parallel_setup_cost,parallel_tuple_cost \
  --bonect-cpu-xp-tp-clients 60 \
  --bonect-cpu-xp-ap-clients 8 \
  --bonect-cpu-xp-ap-query-ids 6,8,10,11 \
  --bonect-cpu-xp-ap-query-weights 15,35,35,15 \
  --bonect-cpu-xp-ap-query-sequence 8,10,6,8,10,11,8,10,6,8,10,11 \
  --bonect-cpu-xp-progress-windows 10 \
  --hybench-statement-sql-file AP-6="$ROOT/shell/hybenchxp_cpu_plan_sensitive_v4_ap6_checking_company_group.sql" \
  --hybench-statement-sql-file AP-8="$ROOT/shell/hybenchxp_cpu_plan_sensitive_v4_ap8_customer_saving_checking_filter.sql" \
  --hybench-statement-sql-file AP-9="$ROOT/shell/hybenchxp_cpu_plan_sensitive_v4_ap9_exists_loan_no_trans.sql" \
  --hybench-statement-sql-file AP-10="$ROOT/shell/hybenchxp_cpu_plan_sensitive_v4_ap10_transfer_customer_company_group.sql" \
  --hybench-statement-sql-file AP-11="$ROOT/shell/hybenchxp_cpu_plan_sensitive_v4_ap11_company_category_transfer.sql" \
  --bruteforce-max-iter 24 \
  --bruteforce-early-stop-threshold 24 \
  --bruteforce-candidate-top-k 60 \
  --bruteforce-candidate-replay-top-k 24 \
  --bruteforce-param-discrete-count 5 \
  --bruteforce-range-shrink-factor 0.5 \
  --bruteforce-range-update-strategy binary_interval \
  --bruteforce-range-min-width-pct 1.0 \
  --bonect-pair-probe-mode full-factorial \
  --bonect-pair-probe-max-combination-size 7 \
  --bonect-pair-probe-max-candidates 78125 \
  --final-feedback-max-rounds 24 \
  --phase2-blind-recovery \
  --adaptive-replay-controller-enabled \
  --adaptive-replay-controller-max-budget 24 \
  --adaptive-replay-controller-min-evidence 12 \
  --adaptive-replay-controller-batch-size 6 \
  --adaptive-replay-controller-max-expansions 2 \
  "${RUN_ARGS[@]}"
