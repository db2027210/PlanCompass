#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
runner=${project_root}/shell/run_pg_none_external_baseline.py
r4_retry_runner=${project_root}/shell/run_pg_common_domain_r4_with_retries.sh
verifier=${project_root}/shell/verify_pg_common_domain_frozen_champions.py
external_verifier=${project_root}/shell/verify_pg_external_baseline_authority.py
config=/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260823_algorithm_c_training_boundary_clean_r75/xp_io/optimizer_config.json
r3_root=/disk1/resource_adaptive/pg_common_domain_frozen_champions_20260826/20260826_four_scene_common_domain_r3
r3_pid=${PG_COMMON_DOMAIN_R3_PID:-3591970}
output_root=${PG_XPIO_EXTERNAL_ROOT:-/disk1/resource_adaptive/pg_external_xpio_correct_domain_20260826/20260826_xpio_40cpu24g_r1}
r4_root=${PG_COMMON_DOMAIN_R4_ROOT:-/disk1/resource_adaptive/pg_common_domain_frozen_champions_20260826/20260826_four_scene_common_domain_r4_corrected_xpio}
candidate_budget=${PG_XPIO_EXTERNAL_CANDIDATE_BUDGET:-35}

while kill -0 "${r3_pid}" 2>/dev/null; do
    sleep 20
done

if [[ ! -f "${r3_root}/state.json" ]] || [[ ! -f "${r3_root}/clean_restore_receipt.json" ]]; then
    echo "r3 exited without state or clean restore receipt; refusing external search" >&2
    exit 1
fi
if [[ "$(jq -r '.phase // ""' "${r3_root}/state.json")" == complete ]]; then
    "${python_bin}" -B -u "${verifier}" "${r3_root}"
elif [[ "$(jq -r '.phase // ""' "${r3_root}/state.json")" == failed_closed ]]; then
    interruption_receipt=${r3_root}/interruption_recovery_receipt.json
    if [[ -f "${interruption_receipt}" ]]; then
        jq -e \
            '.contract == "pg_common_domain_interruption_fail_close_v1"
             and .phase == "failed_closed"
             and .diagnostic_only == true
             and .eligible_for_final_comparison == false
             and .interrupted_stage == "common_baseline_end"
             and .interrupted_attempt == 1
             and .interrupted_physical_workload_invocations == 8
             and .valid_replays_before_interruption == 48
             and (.valid_interrupted_physical_workload_invocations | type == "number" and . >= 0 and . < 8)
             and (.technical_failures_charged | type == "number" and . > 0)
             and (.valid_interrupted_physical_workload_invocations + .technical_failures_charged == 8)
             and (.clean_restore_receipt_sha256 | type == "string" and length == 64)' \
            "${interruption_receipt}" >/dev/null
        echo "r3 diagnostic failed closed with all interrupted calls accounted; continuing from clean restore"
    else
        drift_receipt=${r3_root}/baseline_drift_failure_receipt.json
        jq -e \
            '.phase == "failed_closed"
             and .physical_workload_invocations == 56
             and .valid_replays == 56
             and .technical_failures == 0
             and .manual_mutation_after_start == false
             and ((.stage_results | keys | sort) == ["algorithm_a","algorithm_b","algorithm_c","aqetuner","common_baseline_end","common_baseline_start","gptuner"])
             and (.clean_restore_receipt_sha256 | type == "string" and length == 64)
             and (.stage_results as $s
                  | ["job_memory","tpch_memory","xp_cpu","xp_io"]
                  | map(. as $scene
                        | (($s.common_baseline_end.scene_results[$scene].aggregate.db_qps
                            - $s.common_baseline_start.scene_results[$scene].aggregate.db_qps) | fabs)
                          / (($s.common_baseline_end.scene_results[$scene].aggregate.db_qps
                              + $s.common_baseline_start.scene_results[$scene].aggregate.db_qps) / 2.0)
                          * 100.0)
                  | any(. > 10.0))' \
            "${r3_root}/state.json" >/dev/null
        jq -n \
            --arg state_path "${r3_root}/state.json" \
            --arg state_sha256 "$(sha256sum "${r3_root}/state.json" | awk '{print $1}')" \
            --arg clean_restore_path "${r3_root}/clean_restore_receipt.json" \
            --arg clean_restore_sha256 "$(jq -r '.clean_restore_receipt_sha256' "${r3_root}/state.json")" \
            --argjson drifts "$(jq '.stage_results as $s
                | ["job_memory","tpch_memory","xp_cpu","xp_io"]
                | map(. as $scene
                      | {scene:$scene,
                         start_qps:$s.common_baseline_start.scene_results[$scene].aggregate.db_qps,
                         end_qps:$s.common_baseline_end.scene_results[$scene].aggregate.db_qps,
                         drift_pct:((($s.common_baseline_end.scene_results[$scene].aggregate.db_qps
                                      - $s.common_baseline_start.scene_results[$scene].aggregate.db_qps) | fabs)
                                    / (($s.common_baseline_end.scene_results[$scene].aggregate.db_qps
                                        + $s.common_baseline_start.scene_results[$scene].aggregate.db_qps) / 2.0)
                                    * 100.0)})' "${r3_root}/state.json")" \
            '{contract:"pg_common_domain_baseline_drift_failure_v1",
              phase:"failed_closed",
              diagnostic_only:true,
              eligible_for_final_comparison:false,
              physical_workload_invocations:56,
              valid_replays:56,
              technical_failures:0,
              maximum_allowed_drift_pct:10.0,
              drifts:$drifts,
              state_path:$state_path,
              state_sha256:$state_sha256,
              clean_restore_path:$clean_restore_path,
              clean_restore_receipt_sha256:$clean_restore_sha256}' \
            > "${drift_receipt}.tmp"
        mv "${drift_receipt}.tmp" "${drift_receipt}"
        echo "r3 diagnostic failed closed on baseline drift with 56/56 valid and clean restore; continuing external search"
    fi
else
    echo "r3 is neither complete nor an audited failed-closed diagnostic; refusing external search" >&2
    exit 1
fi
if pgrep -af '[2]0260826_four_scene_common_domain_r3/replays/' >/dev/null; then
    echo "r3 workload children still exist; refusing external search" >&2
    exit 1
fi

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
export PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM=2
export PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S=21600
export PG_ALGORITHM_C_RESTORE_DATABASE_STRATEGY=FILE_COPY
export PG_ALGORITHM_C_RESTORE_MIN_HOST_AVAILABLE_GIB=128
export PG_ALGORITHM_C_RESTORE_MIN_NUMA_FREE_GIB=64
export PG_ALGORITHM_C_RESTORE_MEMORY_POLL_S=2
export GENERIC_RUN_SECONDS=0
export GENERIC_WORKLOAD=hybenchxp

verify_authority() {
    local method=${1:?method required}
    local authority=${output_root}/${method}/xp_io/authority_manifest.json
    [[ -f "${authority}" ]] && jq -e \
        --arg method "${method}" \
        --argjson budget "${candidate_budget}" \
        '.contract == "pg_external_four_scene_baseline_authority_v2"
         and .phase == "complete"
         and .method == $method
         and .scene == "xp_io"
         and .target == "io"
         and .candidate_budget == $budget
         and .candidate_physical_invocations == $budget
         and .replay_parallelism == 2
         and .manual_mutation_after_start == false
         and .parameter_names == ["seq_page_cost", "random_page_cost", "cpu_tuple_cost", "cpu_index_tuple_cost", "cpu_operator_cost", "parallel_setup_cost", "parallel_tuple_cost"]
         and .comparison_qps_source == "validated_champion_two_worker_aggregate"
         and .validated_champion.physical_valid == true
         and .validated_champion.parallel_worker_aggregate.worker_count == 2
         and (.validated_champion.params == .frozen_search_champion.params)
         and (.clean_restore_receipt_sha256 | type == "string" and length == 64)
         and .execution_domain_guard.contract == "pg_external_search_execution_domain_guard_v1"
         and (.execution_domain_guard.container_resource_semantic_sha256 | type == "string" and length == 64)
         and (.execution_domain_guard.fixed_guc_semantic_sha256 | type == "string" and length == 64)
         and (.final_execution_domain_receipts.container_resource_semantic_sha256 == .execution_domain_guard.container_resource_semantic_sha256)
         and (.final_execution_domain_receipts.fixed_guc_semantic_sha256 == .execution_domain_guard.fixed_guc_semantic_sha256)' \
        "${authority}" >/dev/null
}

run_method() {
    local method=${1:?method required}
    local seed=${2:?seed required}
    local output_dir=${output_root}/${method}/xp_io
    local log_path=${output_root}/${method}/xp_io.log
    local attempt
    mkdir -p "${output_dir}"
    if verify_authority "${method}"; then
        echo "${method}/xp_io: verified authority already complete"
        return 0
    fi
    for attempt in 1 2 3; do
        echo "${method}/xp_io: correct-domain search attempt ${attempt}/3"
        if taskset -c 144-159 \
            "${python_bin}" -B -u "${runner}" \
            --method "${method}" \
            --scene xp_io \
            --target io \
            --config "${config}" \
            --output-dir "${output_dir}" \
            --candidate-budget "${candidate_budget}" \
            --seed "${seed}" \
            >> "${log_path}" 2>&1 && verify_authority "${method}"; then
            echo "${method}/xp_io: authority verified"
            return 0
        fi
        if (( attempt < 3 )); then
            sleep 30
        fi
    done
    return 1
}

# The same two PostgreSQL workers are shared by the two methods, so searches
# are deliberately sequential.  Each physical wave still evaluates two
# distinct candidates in parallel, one per Docker worker.
run_method gptuner 20260834
run_method aqetuner 20260844

"${python_bin}" -B -u "${external_verifier}" \
    "${output_root}/gptuner/xp_io" \
    --config "${config}" \
    --output "${output_root}/gptuner/xp_io/authority_verification.json"
"${python_bin}" -B -u "${external_verifier}" \
    "${output_root}/aqetuner/xp_io" \
    --config "${config}" \
    --output "${output_root}/aqetuner/xp_io/authority_verification.json"

jq -n \
    --arg root "${output_root}" \
    --arg config "${config}" \
    --arg completed_at "$(date --iso-8601=seconds)" \
    --argjson budget "${candidate_budget}" \
    '{contract: "pg_xpio_external_correct_domain_pipeline_v1",
      phase: "complete",
      scene: "xp_io",
      target: "io",
      methods: ["gptuner", "aqetuner"],
      configuration: $config,
      searchable_parameters: ["seq_page_cost", "random_page_cost", "cpu_tuple_cost", "cpu_index_tuple_cost", "cpu_operator_cost", "parallel_setup_cost", "parallel_tuple_cost"],
      candidate_budget_per_method: $budget,
      replay_parallelism: 2,
      method_overlap: false,
      authorities: {
        gptuner: ($root + "/gptuner/xp_io/authority_manifest.json"),
        aqetuner: ($root + "/aqetuner/xp_io/authority_manifest.json")
      },
      completed_at: $completed_at}' \
    > "${output_root}/pipeline_authority.json.tmp"
mv "${output_root}/pipeline_authority.json.tmp" "${output_root}/pipeline_authority.json"

echo "XP-I/O GPTuner and AQETuner searches complete in the corrected resource domain"

gptuner_authority=${output_root}/gptuner/xp_io/authority_manifest.json
aqetuner_authority=${output_root}/aqetuner/xp_io/authority_manifest.json
if [[ -e "${r4_root}" ]]; then
    echo "r4 root already exists; refusing to overwrite ${r4_root}" >&2
    exit 1
fi
tmux -L pg_common_domain_20260826_r4 new-session -d \
    -s corrected_xpio \
    -c "${project_root}" \
    "exec env PYTHONPATH=${project_root} PYTHONUNBUFFERED=1 CUDA_VISIBLE_DEVICES= BONECT_SHARED_SSD_COORDINATION_ENABLED=0 BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE=none BINARY_PLAN_TUNING_CPU_MEMORY_PARALLELISM=2 BINARY_PLAN_TUNING_IO_PARALLELISM=2 BINARY_PLAN_TUNING_PARALLEL_BASELINE=1 BINARY_PLAN_TUNING_PARALLEL_BASELINE_PARALLELISM=2 PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM=2 PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S=21600 PG_ALGORITHM_C_RESTORE_DATABASE_STRATEGY=FILE_COPY PG_ALGORITHM_C_RESTORE_MIN_HOST_AVAILABLE_GIB=128 PG_ALGORITHM_C_RESTORE_MIN_NUMA_FREE_GIB=64 PG_ALGORITHM_C_RESTORE_MEMORY_POLL_S=2 GENERIC_RUN_SECONDS=0 PROJECT_ROOT=${project_root} PYTHON_BIN=${python_bin} PG_COMMON_DOMAIN_R4_ROOT=${r4_root} PG_GPTUNER_XPIO_AUTHORITY=${gptuner_authority} PG_AQETUNER_XPIO_AUTHORITY=${aqetuner_authority} taskset -c 104-131,144-159 bash ${r4_retry_runner}"
r4_pid=$(tmux -L pg_common_domain_20260826_r4 display-message -p -t corrected_xpio '#{pane_pid}')
tmux -L pg_common_domain_20260826_r4_postprocess new-session -d \
    -s final_artifacts \
    -c "${project_root}" \
    "exec env PG_COMMON_DOMAIN_R4_PID=${r4_pid} PG_COMMON_DOMAIN_R4_ROOT=${r4_root} PG_XPIO_EXTERNAL_ROOT=${output_root} bash ${project_root}/shell/postprocess_pg_common_domain_r4.sh >> /tmp/pg_common_domain_r4_postprocess_20260826.log 2>&1"

jq -n \
    --arg external_root "${output_root}" \
    --arg r4_root "${r4_root}" \
    --arg launched_at "$(date --iso-8601=seconds)" \
    --argjson r4_pid "${r4_pid}" \
    '{contract: "pg_corrected_xpio_to_common_domain_handoff_v1",
      phase: "r4_launched",
      external_search_root: $external_root,
      final_common_domain_root: $r4_root,
      final_common_domain_pid: $r4_pid,
      final_replay_uses_corrected_xpio_authorities: true,
      launched_at: $launched_at}' \
    > "${output_root}/r4_handoff_authority.json"

echo "launched final r4 common-domain replay at $(date --iso-8601=seconds)"
