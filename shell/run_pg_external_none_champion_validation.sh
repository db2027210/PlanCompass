#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
runner=${project_root}/shell/run_pg_none_external_baseline.py
config=${PG_EXTERNAL_NONE_CONFIG:-/disk1/resource_adaptive/pg_algorithm_bc_none_20260825/20260825_native_xp_x1x1_none_current_r1/algorithm_c_none/optimizer_config.json}
source_root=${PG_EXTERNAL_NONE_SOURCE_ROOT:-/disk1/resource_adaptive/pg_external_none_baselines_20260825/20260825_gptuner_aqetuner_none_r1}
output_root=${PG_EXTERNAL_NONE_OUTPUT_ROOT:-/disk1/resource_adaptive/pg_external_none_baselines_20260825/20260825_gptuner_aqetuner_none_champion_validation_v2}
candidate_budget=35

mkdir -p "${output_root}"

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
export GENERIC_WORKLOAD=hybenchxp

verify_authority() {
    local method=${1:?method required}
    local authority=${output_root}/${method}/authority_manifest.json
    jq -e \
        --arg method "${method}" \
        --argjson budget "${candidate_budget}" \
        '.contract == "pg_external_four_scene_baseline_authority_v2"
         and .phase == "complete"
         and .method == $method
         and .scene == "none"
         and .target == "none"
         and .candidate_budget == $budget
         and .candidate_physical_invocations == $budget
         and .physical_workload_invocations == 41
         and .replay_parallelism == 2
         and .manual_mutation_after_start == false
         and .comparison_qps_source == "validated_champion_two_worker_aggregate"
         and .validated_champion.physical_valid == true
         and .validated_champion.parallel_worker_aggregate.worker_count == 2
         and (.validated_champion.parallel_worker_aggregate.worker_keys == ["w1", "w2"])
         and (.validated_champion.params == .frozen_search_champion.params)
         and (.method_metadata.completed_search_import.contract
              == "pg_external_completed_search_import_v1")
         and (.method_metadata.completed_search_import.source_tree_modified == false)
         and (.method_metadata.completed_search_import.search_workload_replayed == false)
         and (.clean_restore_receipt_sha256 | type == "string" and length == 64)' \
        "${authority}" >/dev/null
}

run_method() {
    local method=${1:?method required}
    local source_state=${source_root}/${method}/state.json
    local output_dir=${output_root}/${method}
    local log_path=${output_root}/${method}.log
    mkdir -p "${output_dir}"
    if [[ -f "${output_dir}/authority_manifest.json" ]] && \
       verify_authority "${method}"; then
        echo "none/${method}: verified authority already complete"
        return 0
    fi
    taskset -c 144-159 \
        "${python_bin}" -B -u "${runner}" \
        --method "${method}" \
        --scene none \
        --target none \
        --config "${config}" \
        --output-dir "${output_dir}" \
        --candidate-budget "${candidate_budget}" \
        --seed 20260825 \
        --import-completed-search-state "${source_state}" \
        >> "${log_path}" 2>&1
    verify_authority "${method}"
}

# The two methods share the same two PostgreSQL workers.  Run them
# sequentially; within each method the baseline and champion are replayed on
# both Docker workers concurrently.
run_method gptuner
run_method aqetuner

jq -n \
    --arg contract pg_external_none_champion_validation_pipeline_v1 \
    --arg output_root "${output_root}" \
    --arg source_root "${source_root}" \
    --arg config "${config}" \
    --arg selected_scene xp_cpu \
    --arg selection_reason "Algorithm C highest four-scene QPS improvement" \
    --arg completed_at "$(date --iso-8601=seconds)" \
    '{contract: $contract,
      phase: "complete",
      methods: ["gptuner", "aqetuner"],
      selected_representative_scene: $selected_scene,
      selection_reason: $selection_reason,
      none_workload: "native HyBench XP SF10x x1x1 full default",
      source_search_root: $source_root,
      output_root: $output_root,
      config: $config,
      candidate_search_replayed: false,
      fresh_baseline_physical_invocations_per_method: 4,
      fresh_champion_physical_invocations_per_method: 2,
      physical_replay_parallelism: 2,
      completed_at: $completed_at}' \
    > "${output_root}/pipeline_authority.json"

echo "None external champion validation complete: ${output_root}"
