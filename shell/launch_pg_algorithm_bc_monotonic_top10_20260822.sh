#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
runner=${project_root}/shell/run_pg_algorithm_bc_monotonic_top10_20260822_lane.sh
supervisor=${project_root}/shell/run_pg_algorithm_c_cohort_support_supervisor.py
handoff=${project_root}/shell/handoff_pg_algorithm_c_r69_to_b_r5.py
final_audit=${project_root}/shell/watch_pg_algorithm_bc_monotonic_top10_final_audit_20260822.py
algorithm=${1:?usage: launch_pg_algorithm_bc_monotonic_top10_20260822.sh B_OR_C}

case "${algorithm}" in
    C)
        campaign_root=/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260822_algorithm_c_monotonic_qps_top10_current_interval_clean_r74
        cohort_prefix=20260822_algorithm_c_monotonic_top10_r74
        session_prefix=pgc_monotop10_r74
        state_name=algorithm_c_state.json
        history_name=algorithm_c.jsonl
        controller_name=run_pg_algorithm_c_scene.py
        ;;
    B)
        campaign_root=/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/20260822_algorithm_b_monotonic_qps_top10_current_interval_clean_r10
        cohort_prefix=20260822_algorithm_b_monotonic_top10_r10
        session_prefix=pgb_monotop10_r10
        state_name=algorithm_b_state.json
        history_name=algorithm_b.jsonl
        controller_name=run_pg_algorithm_b_scene.py
        ;;
    *)
        echo "unknown algorithm: ${algorithm}" >&2
        exit 2
        ;;
esac

minimum_tmp_available_bytes=$((4 * 1024 * 1024 * 1024))
read -r tmp_available_blocks tmp_block_size < <(stat -f -c '%a %S' /tmp)
tmp_available_bytes=$((tmp_available_blocks * tmp_block_size))
if (( tmp_available_bytes < minimum_tmp_available_bytes )); then
    echo "Algorithm ${algorithm} launch blocked: /tmp available=${tmp_available_bytes} required=${minimum_tmp_available_bytes}" >&2
    exit 1
fi
echo "Algorithm ${algorithm} /tmp preflight passed: available=${tmp_available_bytes} required=${minimum_tmp_available_bytes}"

test -f "${campaign_root}/fresh_campaign_manifest.json"
session_lanes=(job tpch xp_cpu xp_io support)
if [[ "${algorithm}" == "C" ]]; then
    session_lanes+=(handoff audit)
fi
for lane in "${session_lanes[@]}"; do
    if tmux has-session -t "${session_prefix}_${lane}" 2>/dev/null; then
        echo "tmux session already exists: ${session_prefix}_${lane}" >&2
        exit 1
    fi
done
for scene in job_memory tpch_memory xp_cpu xp_io; do
    test -f "${campaign_root}/${scene}/optimizer_config.json"
    test ! -e "${campaign_root}/${scene}/${state_name}"
    test ! -e "${campaign_root}/${scene}/${history_name}"
done

for lane in job tpch xp_cpu xp_io; do
    tmux new-session -d -s "${session_prefix}_${lane}" \
        -c "${project_root}" "exec ${runner} ${algorithm} ${lane}"
done

supervisor_args=(
    --campaign-root "${campaign_root}"
    --cohort-id "${cohort_prefix}_candidate"
    --cohort-size 4
    --timeout-s 1800
    --terminal-wait-timeout-s 86400
)
if [[ "${algorithm}" == "B" ]]; then
    supervisor_args+=(
        --state-file-name "${state_name}"
        --history-file-name "${history_name}"
        --controller-script-name "${controller_name}"
    )
fi
tmux new-session -d -s "${session_prefix}_support" -c "${project_root}" \
    "exec ${python_bin} -B -u ${supervisor} ${supervisor_args[*]} >> ${campaign_root}/cohort_support/supervisor.log 2>&1"

if [[ "${algorithm}" == "C" ]]; then
    tmux new-session -d -s "${session_prefix}_handoff" -c "${project_root}" \
        "exec ${python_bin} -B -u ${handoff} >> ${campaign_root}/algorithm_c_then_b_handoff.log 2>&1"
    tmux new-session -d -s "${session_prefix}_audit" -c "${project_root}" \
        "exec ${python_bin} -B -u ${final_audit} >> ${campaign_root}/algorithm_bc_monotonic_top10_final_audit.log 2>&1"
fi

tmux list-sessions -F '#S' | sort | grep "^${session_prefix}_"
