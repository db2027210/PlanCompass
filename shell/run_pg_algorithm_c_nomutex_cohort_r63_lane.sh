#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260813_algorithm_c_prephysical_history_clean_r63
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
controller=${project_root}/shell/run_pg_algorithm_c_scene.py
lane=${1:?usage: run_pg_algorithm_c_nomutex_cohort_r63_lane.sh LANE}

export PYTHONUNBUFFERED=1
export PYTHONPATH=${project_root}
export BONECT_SHARED_SSD_COORDINATION_ENABLED=0
export BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE=none
export BONECT_ALGORITHM_C_BASELINE_COHORT_ID=20260813T2350_nomutex_r63
export BONECT_ALGORITHM_C_BASELINE_COHORT_SIZE=4
export BONECT_ALGORITHM_C_BASELINE_COHORT_TIMEOUT_S=1800
export BONECT_ALGORITHM_C_CANDIDATE_COHORT_ID=20260813T2350_nomutex_candidate_r63
export BONECT_ALGORITHM_C_CANDIDATE_COHORT_SIZE=4
export BONECT_ALGORITHM_C_CANDIDATE_COHORT_TIMEOUT_S=1800
export PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S=21600

case "${lane}" in
    job)
        output_dir=${campaign_root}/job_memory
        target=memory
        controller_cpus=176-179
        export GENERIC_RUN_SECONDS=0 GENERIC_WORKLOAD=job
        export JOB_WORKERS=2 JOB_MAX_CONCURRENT_LONG_QUERIES=1
        export JOB_MAX_CONCURRENT_LONG_QUERIES_PER_FAMILY=1 JOB_TARGET_WALL_TIME_S=900
        export JOB_FIXED_SCHEDULE_SHA256=950fde0959033c8da695fae91aedf56481008ddf5de4c2e781fb00b30fb36513
        ;;
    tpch)
        output_dir=${campaign_root}/tpch_memory
        target=memory
        controller_cpus=180-183
        export GENERIC_RUN_SECONDS=0 GENERIC_WORKLOAD=tpch
        export BENCHBASE_SOURCE_ROOT=/home/anonymous/benchbase-postgres
        export BENCHBASE_RUNTIME_ROOT=${campaign_root}/benchbase_runtime
        export BENCHBASE_SCALEFACTOR=5 BENCHBASE_TPCH_WORKERS=1
        export BENCHBASE_JAVA_XMX=-Xmx2g TPCH_TARGET_WALL_TIME_S=900
        ;;
    xp_cpu)
        output_dir=${campaign_root}/xp_cpu
        target=cpu
        controller_cpus=128-131
        export GENERIC_RUN_SECONDS=0 GENERIC_WORKLOAD=hybenchxp
        ;;
    xp_io)
        output_dir=${campaign_root}/xp_io
        target=io
        controller_cpus=144-159
        export GENERIC_RUN_SECONDS=0 GENERIC_WORKLOAD=hybenchxp
        ;;
    *)
        echo "unknown lane: ${lane}" >&2
        exit 2
        ;;
esac

exec taskset -c "${controller_cpus}" \
    "${python_bin}" -B -u "${controller}" \
    --config "${output_dir}/optimizer_config.json" \
    --output-dir "${output_dir}" \
    --target "${target}" \
    >> "${output_dir}/controller.log" 2>&1
