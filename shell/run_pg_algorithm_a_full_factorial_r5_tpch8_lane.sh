#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/20260816_algorithm_a_full_factorial_globalrank_labels_clean_r5
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
output_dir=${campaign_root}/tpch_memory

export PYTHONUNBUFFERED=1
export PYTHONPATH=${project_root}
export BONECT_SHARED_SSD_COORDINATION_ENABLED=0
export BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE=none
export BONECT_ALGORITHM_C_BASELINE_COHORT_ID=20260820_algorithm_a_tpch_workers8_released_pg_slots_transition
export BONECT_ALGORITHM_C_BASELINE_COHORT_SIZE=1
export BONECT_ALGORITHM_C_BASELINE_COHORT_TIMEOUT_S=1800
export BONECT_ALGORITHM_C_CANDIDATE_COHORT_SIZE=1
export BONECT_ALGORITHM_C_CANDIDATE_COHORT_TIMEOUT_S=1800
unset BONECT_ALGORITHM_C_CANDIDATE_COHORT_ID || true
export PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM=8
export PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S=21600
export GENERIC_RUN_SECONDS=0
export GENERIC_WORKLOAD=tpch
export BENCHBASE_SOURCE_ROOT=/home/anonymous/benchbase-postgres
export BENCHBASE_RUNTIME_ROOT=${campaign_root}/benchbase_runtime
export BENCHBASE_SCALEFACTOR=5
export BENCHBASE_TPCH_WORKERS=1
export BENCHBASE_JAVA_XMX=-Xmx2g
export TPCH_TARGET_WALL_TIME_S=900

exec taskset -c 180-183 \
    "${python_bin}" -B -u "${project_root}/shell/run_pg_algorithm_a_scene.py" \
    --config "${output_dir}/optimizer_config.json" \
    --output-dir "${output_dir}" \
    --target memory \
    >> "${output_dir}/controller.log" 2>&1
