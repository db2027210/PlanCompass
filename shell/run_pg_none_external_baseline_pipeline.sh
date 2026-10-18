#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
runner=${project_root}/shell/run_pg_none_external_baseline.py
lane_runner=${project_root}/shell/run_pg_algorithm_bc_none_lane.sh
campaign_root=${PG_BC_NONE_CAMPAIGN_ROOT:-/disk1/resource_adaptive/pg_algorithm_bc_none_20260825/20260825_native_xp_x1x1_none_current_r1}
baseline_root=${PG_NONE_BASELINE_ROOT:-/disk1/resource_adaptive/pg_external_none_baselines_20260825/20260825_gptuner_aqetuner_none_r1}
config=${PG_NONE_BASELINE_CONFIG:-${campaign_root}/algorithm_c_none/optimizer_config.json}
resume_socket=${PG_BC_NONE_RESUME_TMUX_SOCKET:-pg_bc_none_20260825_r1_resume_after_baselines}
candidate_budget=${PG_NONE_BASELINE_CANDIDATE_BUDGET:-35}

mkdir -p "${baseline_root}"

export PYTHONPATH=${project_root}
export PYTHONUNBUFFERED=1
export CUDA_VISIBLE_DEVICES=
export BONECT_SHARED_SSD_COORDINATION_ENABLED=0
export BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE=none
export BINARY_PLAN_TUNING_NONE_PARALLELISM=2
export BINARY_PLAN_TUNING_PARALLEL_BASELINE=1
export BINARY_PLAN_TUNING_PARALLEL_BASELINE_PARALLELISM=2
export PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM=2
export PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S=21600
export GENERIC_RUN_SECONDS=0
export GENERIC_WORKLOAD=hybenchxp

for required in \
    "${runner}" \
    "${lane_runner}" \
    "${config}" \
    "${project_root}/baselines/GPTuner_official/.git" \
    "${project_root}/baselines/AQETuner_official/.git"; do
    if [[ ! -e "${required}" ]]; then
        echo "missing required baseline input: ${required}" >&2
        exit 2
    fi
done

campaign_owner_active=0
for lane in algorithm_b_none algorithm_c_none; do
    pid_file=${campaign_root}/${lane}/controller.pid
    if [[ ! -f "${pid_file}" ]]; then
        continue
    fi
    read -r owner_pid < "${pid_file}"
    if [[ "${owner_pid}" =~ ^[0-9]+$ ]] && kill -0 "${owner_pid}" 2>/dev/null; then
        campaign_owner_active=1
        echo "active campaign owner: ${lane} pid=${owner_pid}" >&2
    fi
done
if (( campaign_owner_active != 0 )); then
    echo "current None B/C controllers still own the experiment workers" >&2
    echo "clean-stop them at a wave boundary before launching this pipeline" >&2
    exit 3
fi
if [[ "${PG_NONE_BASELINE_PREFLIGHT_ONLY:-0}" == "1" ]]; then
    echo "baseline pipeline preflight passed; launch explicitly disabled"
    exit 0
fi

run_method() {
    local method=${1:?method required}
    local output_dir=${baseline_root}/${method}
    local authority=${output_dir}/authority_manifest.json
    if [[ -f "${authority}" ]] && \
       jq -e '.phase == "complete" and .candidate_physical_invocations == .candidate_budget' \
          "${authority}" >/dev/null; then
        echo "${method}: verified authority already complete"
        return
    fi
    echo "${method}: starting two-Docker native HyBench XP None baseline"
    taskset -c 132-135 "${python_bin}" -B -u "${runner}" \
        --method "${method}" \
        --config "${config}" \
        --output-dir "${output_dir}" \
        --candidate-budget "${candidate_budget}" \
        --seed 20260825
    jq -e \
        --argjson budget "${candidate_budget}" \
        '.phase == "complete"
         and .candidate_budget == $budget
         and .candidate_physical_invocations == $budget
         and .replay_parallelism == 2
         and .manual_mutation_after_start == false
         and (.clean_restore_receipt_sha256 | type == "string")' \
        "${authority}" >/dev/null
    echo "${method}: authority verified"
}

run_method gptuner
run_method aqetuner

jq -n \
    --arg contract pg_external_none_baseline_pipeline_v1 \
    --arg gptuner "${baseline_root}/gptuner/authority_manifest.json" \
    --arg aqetuner "${baseline_root}/aqetuner/authority_manifest.json" \
    --arg current_campaign "${campaign_root}" \
    --argjson candidate_budget "${candidate_budget}" \
    --arg completed_at "$(date --iso-8601=seconds)" \
    '{contract: $contract,
      methods: {gptuner: $gptuner, aqetuner: $aqetuner},
      workload: "native_hybench_xp_sf10x_x1x1_full_default",
      replay_parallelism: 2,
      candidate_budget_per_method: $candidate_budget,
      current_none_campaign_to_resume: $current_campaign,
      completed_at: $completed_at}' \
    > "${baseline_root}/pipeline_authority.json.tmp"
mv "${baseline_root}/pipeline_authority.json.tmp" \
   "${baseline_root}/pipeline_authority.json"

for lane in algorithm_b_none algorithm_c_none; do
    state_name=algorithm_b_state.json
    if [[ "${lane}" == algorithm_c_none ]]; then
        state_name=algorithm_c_state.json
    fi
    state=${campaign_root}/${lane}/${state_name}
    if [[ ! -f "${state}" ]]; then
        echo "missing resumable None state: ${state}" >&2
        exit 4
    fi
    if jq -e '.phase == "complete" or .stage == "complete"' "${state}" >/dev/null; then
        echo "${lane}: already complete, resume skipped"
        continue
    fi
    if tmux -L "${resume_socket}" has-session -t "${lane}" 2>/dev/null; then
        echo "${lane}: resume session already exists"
        continue
    fi
    command="exec env PG_BC_NONE_CAMPAIGN_ROOT=${campaign_root} ${lane_runner} ${lane}"
    tmux -L "${resume_socket}" new-session -d -s "${lane}" \
        -c "${project_root}" "${command}"
    echo "${lane}: resumed from existing formal state"
done
tmux -L "${resume_socket}" set-option -g remain-on-exit on 2>/dev/null || true

echo "external baselines complete; resumable None B/C lanes launched"
