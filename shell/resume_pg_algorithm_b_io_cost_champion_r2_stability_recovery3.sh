#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/20260815_algorithm_b_grid15_io_cost_champion_clean_r2
runner=${project_root}/shell/run_pg_algorithm_b_io_cost_champion_r2_lane.sh
supervisor=${project_root}/shell/run_pg_algorithm_c_cohort_support_supervisor.py
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
cohort_id=20260815_algorithm_b_io_cost_champion_candidate_r2_recovery3

for lane_dir in job_memory tpch_memory xp_io; do
    state=${campaign_root}/${lane_dir}/algorithm_b_state.json
    test "$(jq -r '.accepted' "${state}")" = 7
    test "$(jq -r '.phase' "${state}")" = stopped_by_signal_clean
done
test "$(jq -r '.phase' "${campaign_root}/xp_cpu/algorithm_b_state.json")" = complete
test -f "${campaign_root}/xp_cpu/authority_manifest.json"

for session in pgb_iochamp_r2_recovery3_job pgb_iochamp_r2_recovery3_tpch \
    pgb_iochamp_r2_recovery3_xp_io pgb_iochamp_r2_recovery3_support; do
    if tmux has-session -t "${session}" 2>/dev/null; then
        echo "tmux session already exists: ${session}" >&2
        exit 1
    fi
done

tmux new-session -d -s pgb_iochamp_r2_recovery3_support -c "${project_root}" \
    "export BONECT_SHARED_SSD_COORDINATION_ENABLED=0 BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE=none; exec ${python_bin} -B -u ${supervisor} --campaign-root ${campaign_root} --cohort-id ${cohort_id} --cohort-size 4 --timeout-s 1800 --state-file-name algorithm_b_state.json --history-file-name algorithm_b.jsonl --controller-script-name run_pg_algorithm_b_scene.py >> ${campaign_root}/cohort_support/recovery3_supervisor.log 2>&1"

for lane in job tpch xp_io; do
    tmux new-session -d -s "pgb_iochamp_r2_recovery3_${lane}" -c "${project_root}" \
        "export BONECT_ALGORITHM_C_CANDIDATE_COHORT_ID=${cohort_id}; exec ${runner} ${lane}"
done

tmux list-sessions -F '#S' | sort | rg '^pgb_iochamp_r2_recovery3_'
