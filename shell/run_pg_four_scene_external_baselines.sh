#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
runner=${project_root}/shell/run_pg_none_external_baseline.py
config_root=${PG_EXTERNAL_CONFIG_ROOT:-/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260823_algorithm_c_training_boundary_clean_r75}
baseline_root=${PG_EXTERNAL_BASELINE_ROOT:-/disk1/resource_adaptive/pg_external_four_scene_baselines_20260825/20260825_gptuner_aqetuner_four_r1}
candidate_budget=${PG_EXTERNAL_CANDIDATE_BUDGET:-35}
job_schedule_manifest=${baseline_root}/job_fixed_schedule_authority.json

mkdir -p "${baseline_root}"

jq '.algorithm_c.workload_contract.benchmark_concurrency.fixed_schedule' \
    "${config_root}/job_memory/optimizer_config.json" \
    > "${job_schedule_manifest}.tmp"
mv "${job_schedule_manifest}.tmp" "${job_schedule_manifest}"

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
export JOB_FIXED_SCHEDULE_MANIFEST=${job_schedule_manifest}
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

for required in \
    "${runner}" \
    "${project_root}/baselines/GPTuner_official/.git" \
    "${project_root}/baselines/AQETuner_official/.git" \
    "${config_root}/job_memory/optimizer_config.json" \
    "${config_root}/tpch_memory/optimizer_config.json" \
    "${config_root}/xp_cpu/optimizer_config.json" \
    "${config_root}/xp_io/optimizer_config.json"; do
    if [[ ! -e "${required}" ]]; then
        echo "missing required external-baseline input: ${required}" >&2
        exit 2
    fi
done

verify_authority() {
    local authority=${1:?authority path required}
    local method=${2:?method required}
    local scene=${3:?scene required}
    local target=${4:?target required}
    jq -e \
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
         and (.validated_champion.params == .frozen_search_champion.params)
         and (.clean_restore_receipt_sha256 | type == "string" and length == 64)' \
        "${authority}" >/dev/null
}

run_scene() {
    local method=${1:?method required}
    local scene=${2:?scene required}
    local target=${3:?target required}
    local controller_cpuset=${4:?controller cpuset required}
    local generic_workload=${5:?workload required}
    local seed=${6:?seed required}
    local config=${config_root}/${scene}/optimizer_config.json
    local output_dir=${baseline_root}/${method}/${scene}
    local authority=${output_dir}/authority_manifest.json
    local log_path=${baseline_root}/${method}/${scene}.log

    mkdir -p "${output_dir}"
    if [[ -f "${authority}" ]] && \
       verify_authority "${authority}" "${method}" "${scene}" "${target}"; then
        echo "${method}/${scene}: verified authority already complete"
        return 0
    fi
    local attempt=1
    local attempt_limit=3
    while (( attempt <= attempt_limit )); do
        echo "${method}/${scene}: starting/resuming two-Docker search and champion verification (attempt ${attempt}/${attempt_limit})"
        if env GENERIC_WORKLOAD="${generic_workload}" \
            taskset -c "${controller_cpuset}" \
            "${python_bin}" -B -u "${runner}" \
            --method "${method}" \
            --scene "${scene}" \
            --target "${target}" \
            --config "${config}" \
            --output-dir "${output_dir}" \
            --candidate-budget "${candidate_budget}" \
            --seed "${seed}" \
            >> "${log_path}" 2>&1 && \
           verify_authority "${authority}" "${method}" "${scene}" "${target}"; then
            echo "${method}/${scene}: authority verified"
            return 0
        fi
        echo "${method}/${scene}: resumable attempt ${attempt} failed" >&2
        attempt=$((attempt + 1))
        if (( attempt <= attempt_limit )); then
            sleep 30
        fi
    done
    return 1
}

run_method_cohort() {
    local method=${1:?method required}
    local method_failed=0
    local job_pid tpch_pid cpu_pid io_pid

    mkdir -p "${baseline_root}/${method}"
    run_scene "${method}" job_memory memory 176-179 job 20260831 &
    job_pid=$!
    run_scene "${method}" tpch_memory memory 180-183 tpch 20260832 &
    tpch_pid=$!
    run_scene "${method}" xp_cpu cpu 128-131 hybenchxp 20260833 &
    cpu_pid=$!
    run_scene "${method}" xp_io io 144-159 hybenchxp 20260834 &
    io_pid=$!

    for lane_pid in "${job_pid}" "${tpch_pid}" "${cpu_pid}" "${io_pid}"; do
        if ! wait "${lane_pid}"; then
            method_failed=1
        fi
    done
    if (( method_failed != 0 )); then
        echo "${method}: at least one scene failed; inspect per-scene logs" >&2
        return 1
    fi
}

# Keep the physical co-load comparable: all four GPTuner scenes finish before
# the four AQETuner scenes begin.  Within every scene, exactly two candidates
# are replayed concurrently on its two isolated PostgreSQL containers.
run_method_cohort gptuner
run_method_cohort aqetuner

jq -n \
    --arg contract pg_external_four_scene_baseline_pipeline_v1 \
    --arg root "${baseline_root}" \
    --arg config_root "${config_root}" \
    --argjson candidate_budget "${candidate_budget}" \
    --arg completed_at "$(date --iso-8601=seconds)" \
    '{contract: $contract,
      methods: ["gptuner", "aqetuner"],
      scenes: ["job_memory", "tpch_memory", "xp_cpu", "xp_io"],
      configuration_root: $config_root,
      candidate_budget_per_method_scene: $candidate_budget,
      physical_replay_parallelism_per_scene: 2,
      final_comparison_contract: "frozen_champion_two_worker_aggregate_only",
      authorities: {
        gptuner: {
          job_memory: ($root + "/gptuner/job_memory/authority_manifest.json"),
          tpch_memory: ($root + "/gptuner/tpch_memory/authority_manifest.json"),
          xp_cpu: ($root + "/gptuner/xp_cpu/authority_manifest.json"),
          xp_io: ($root + "/gptuner/xp_io/authority_manifest.json")
        },
        aqetuner: {
          job_memory: ($root + "/aqetuner/job_memory/authority_manifest.json"),
          tpch_memory: ($root + "/aqetuner/tpch_memory/authority_manifest.json"),
          xp_cpu: ($root + "/aqetuner/xp_cpu/authority_manifest.json"),
          xp_io: ($root + "/aqetuner/xp_io/authority_manifest.json")
        }
      },
      completed_at: $completed_at}' \
    > "${baseline_root}/pipeline_authority.json.tmp"
mv "${baseline_root}/pipeline_authority.json.tmp" \
   "${baseline_root}/pipeline_authority.json"

echo "all four-scene GPTuner/AQETuner final champions verified"
