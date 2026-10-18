#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=${PG_BC_NONE_CAMPAIGN_ROOT:-/disk1/resource_adaptive/pg_algorithm_bc_none_20260825/20260825_native_xp_x1x1_none_current_r1}
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
lane=${1:?usage: run_pg_algorithm_bc_none_lane.sh algorithm_b_none|algorithm_c_none}

export PYTHONUNBUFFERED=1
export PYTHONPATH=${project_root}
export BONECT_SHARED_SSD_COORDINATION_ENABLED=0
export BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE=none
export BONECT_ALGORITHM_C_BASELINE_COHORT_ID=${PG_BC_NONE_BASELINE_COHORT_ID:-20260825_pg_bc_none_current_r1_baseline}
export BONECT_ALGORITHM_C_BASELINE_COHORT_SIZE=2
export BONECT_ALGORITHM_C_BASELINE_COHORT_TIMEOUT_S=1800
export BONECT_ALGORITHM_C_CANDIDATE_COHORT_SIZE=1
export BINARY_PLAN_TUNING_NONE_PARALLELISM=2
export BINARY_PLAN_TUNING_PARALLEL_BASELINE=1
export BINARY_PLAN_TUNING_PARALLEL_BASELINE_PARALLELISM=2
export PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S=21600
export GENERIC_RUN_SECONDS=0
export GENERIC_WORKLOAD=hybenchxp

case "${lane}" in
    algorithm_b_none)
        controller=${project_root}/shell/run_pg_algorithm_b_scene.py
        controller_cpus=128-129
        ;;
    algorithm_c_none)
        controller=${project_root}/shell/run_pg_algorithm_c_scene.py
        controller_cpus=130-131
        ;;
    *)
        echo "unknown lane: ${lane}" >&2
        exit 2
        ;;
esac

output_dir=${campaign_root}/${lane}
exec taskset -c "${controller_cpus}" \
    "${python_bin}" -B -u "${controller}" \
    --config "${output_dir}/optimizer_config.json" \
    --output-dir "${output_dir}" \
    --target none \
    >> "${output_dir}/controller.log" 2>&1
