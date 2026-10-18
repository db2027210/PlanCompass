#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/20260816_algorithm_a_full_factorial_globalrank_labels_clean_r5
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
output_dir=${campaign_root}/job_memory

export PYTHONUNBUFFERED=1
export PYTHONPATH=${project_root}
export BONECT_SHARED_SSD_COORDINATION_ENABLED=0
export BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE=none
export BONECT_ALGORITHM_C_BASELINE_COHORT_ID=20260819_algorithm_a_job_workers4_transition
export BONECT_ALGORITHM_C_BASELINE_COHORT_SIZE=1
export BONECT_ALGORITHM_C_BASELINE_COHORT_TIMEOUT_S=1800
export BONECT_ALGORITHM_C_CANDIDATE_COHORT_SIZE=1
export BONECT_ALGORITHM_C_CANDIDATE_COHORT_TIMEOUT_S=1800
unset BONECT_ALGORITHM_C_CANDIDATE_COHORT_ID || true
export PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM=4
export PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S=21600
export GENERIC_RUN_SECONDS=0
export GENERIC_WORKLOAD=job
export JOB_WORKERS=2
export JOB_MAX_CONCURRENT_LONG_QUERIES=1
export JOB_MAX_CONCURRENT_LONG_QUERIES_PER_FAMILY=1
export JOB_TARGET_WALL_TIME_S=900
export JOB_FIXED_SCHEDULE_SHA256=950fde0959033c8da695fae91aedf56481008ddf5de4c2e781fb00b30fb36513

exec taskset -c 176-179 \
    "${python_bin}" -B -u "${project_root}/shell/run_pg_algorithm_a_scene.py" \
    --config "${output_dir}/optimizer_config.json" \
    --output-dir "${output_dir}" \
    --target memory \
    >> "${output_dir}/controller.log" 2>&1
