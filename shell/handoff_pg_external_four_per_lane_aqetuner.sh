#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
runner=${project_root}/shell/run_pg_none_external_baseline.py
config_root=${PG_EXTERNAL_CONFIG_ROOT:-/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260823_algorithm_c_training_boundary_clean_r75}
baseline_root=${PG_EXTERNAL_BASELINE_ROOT:-/disk1/resource_adaptive/pg_external_four_scene_baselines_20260825/20260825_gptuner_aqetuner_four_r1}
candidate_budget=35

mkdir -p "${baseline_root}/aqetuner"

export PYTHONPATH=${project_root}
export PYTHONUNBUFFERED=1
export CUDA_VISIBLE_DEVICES=
export BONECT_SHARED_SSD_COORDINATION_ENABLED=0
export BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE=none
export BINARY_PLAN_TUNING_CPU_MEMORY_PARALLELISM=2
export BINARY_PLAN_TUNING_IO_PARALLELISM=2
export BINARY_PLAN_TUNING_PARALLEL_BASELINE=1
export BINARY_PLAN_TUNING_PARALLEL_BASELINE_PARALLELISM=2
export PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM=1
export PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S=21600
export PG_ALGORITHM_C_RESTORE_DATABASE_STRATEGY=FILE_COPY
export PG_ALGORITHM_C_RESTORE_MIN_HOST_AVAILABLE_GIB=128
export PG_ALGORITHM_C_RESTORE_MIN_NUMA_FREE_GIB=64
export PG_ALGORITHM_C_RESTORE_MEMORY_POLL_S=2
export GENERIC_RUN_SECONDS=0
export JOB_FIXED_SCHEDULE_SHA256=950fde0959033c8da695fae91aedf56481008ddf5de4c2e781fb00b30fb36513
export JOB_FIXED_SCHEDULE_MANIFEST=${baseline_root}/job_fixed_schedule_authority.json
export JOB_WORKERS=2
export JOB_MAX_CONCURRENT_LONG_QUERIES=1
export JOB_MAX_CONCURRENT_LONG_QUERIES_PER_FAMILY=1
export JOB_TARGET_WALL_TIME_S=900
export BENCHBASE_SOURCE_ROOT=/home/anonymous/benchbase-postgres
export BENCHBASE_RUNTIME_ROOT=${baseline_root}/benchbase_runtime
export BENCHBASE_SCALEFACTOR=5
export BENCHBASE_TPCH_WORKERS=1
export BENCHBASE_JAVA_XMX=-Xmx2g
export TPCH_TARGET_WALL_TIME_S=900

verify_authority() {
    local method=${1:?method required}
    local scene=${2:?scene required}
    local target=${3:?target required}
    local authority=${baseline_root}/${method}/${scene}/authority_manifest.json
    [[ -f "${authority}" ]] && jq -e \
        --arg method "${method}" \
        --arg scene "${scene}" \
        --arg target "${target}" \
        --argjson budget "${candidate_budget}" \
        '.contract == "pg_external_four_scene_baseline_authority_v2"
         and .phase == "complete"
         and .method == $method
         and .scene == $scene
         and .target == $target
         and .candidate_budget == $budget
         and .candidate_physical_invocations == $budget
         and .replay_parallelism == 2
         and .manual_mutation_after_start == false
         and .comparison_qps_source == "validated_champion_two_worker_aggregate"
         and .validated_champion.physical_valid == true
         and .validated_champion.parallel_worker_aggregate.worker_count == 2
         and (.validated_champion.parallel_worker_aggregate.worker_keys == ["w1", "w2"])
         and (.validated_champion.params == .frozen_search_champion.params)
         and (.clean_restore_receipt_sha256 | type == "string" and length == 64)' \
        "${authority}" >/dev/null
}

run_lane() {
    local scene=${1:?scene required}
    local target=${2:?target required}
    local controller_cpuset=${3:?controller cpuset required}
    local workload=${4:?workload required}
    local seed=${5:?seed required}
    local output_dir=${baseline_root}/aqetuner/${scene}
    local log_path=${baseline_root}/aqetuner/${scene}.log
    local attempt

    while ! verify_authority gptuner "${scene}" "${target}"; do
        sleep 15
    done
    if verify_authority aqetuner "${scene}" "${target}"; then
        echo "aqetuner/${scene}: verified authority already complete"
        return 0
    fi
    mkdir -p "${output_dir}"
    for attempt in 1 2 3; do
        echo "aqetuner/${scene}: lane handoff attempt ${attempt}/3"
        if env GENERIC_WORKLOAD="${workload}" \
            taskset -c "${controller_cpuset}" \
            "${python_bin}" -B -u "${runner}" \
            --method aqetuner \
            --scene "${scene}" \
            --target "${target}" \
            --config "${config_root}/${scene}/optimizer_config.json" \
            --output-dir "${output_dir}" \
            --candidate-budget "${candidate_budget}" \
            --seed "${seed}" \
            >> "${log_path}" 2>&1 && \
           verify_authority aqetuner "${scene}" "${target}"; then
            echo "aqetuner/${scene}: authority verified"
            return 0
        fi
        if (( attempt < 3 )); then
            sleep 30
        fi
    done
    return 1
}

jq -n \
    --arg contract pg_external_per_lane_method_handoff_v1 \
    --arg authorized_at "$(date --iso-8601=seconds)" \
    '{contract: $contract,
      status: "authorized_and_started",
      policy: "start_aqetuner_when_same_scene_gptuner_authority_is_complete",
      user_authorized_mixed_method_shared_ssd_coload: true,
      same_scene_overlap_forbidden: true,
      physical_replay_parallelism_per_scene: 2,
      candidate_budget_per_method_scene: 35,
      authorized_at: $authorized_at}' \
    > "${baseline_root}/per_lane_handoff_authority.json"

run_lane job_memory memory 176-179 job 20260841 &
job_pid=$!
run_lane tpch_memory memory 180-183 tpch 20260842 &
tpch_pid=$!
run_lane xp_cpu cpu 128-131 hybenchxp 20260843 &
cpu_pid=$!
run_lane xp_io io 144-159 hybenchxp 20260844 &
io_pid=$!

failed=0
for lane_pid in "${job_pid}" "${tpch_pid}" "${cpu_pid}" "${io_pid}"; do
    if ! wait "${lane_pid}"; then
        failed=1
    fi
done
if (( failed != 0 )); then
    jq '.status = "failed"' \
        "${baseline_root}/per_lane_handoff_authority.json" \
        > "${baseline_root}/per_lane_handoff_authority.json.tmp"
    mv "${baseline_root}/per_lane_handoff_authority.json.tmp" \
        "${baseline_root}/per_lane_handoff_authority.json"
    exit 1
fi

jq --arg completed_at "$(date --iso-8601=seconds)" \
    '.status = "complete" | .completed_at = $completed_at' \
    "${baseline_root}/per_lane_handoff_authority.json" \
    > "${baseline_root}/per_lane_handoff_authority.json.tmp"
mv "${baseline_root}/per_lane_handoff_authority.json.tmp" \
    "${baseline_root}/per_lane_handoff_authority.json"

echo "AQETuner per-lane handoff complete"
