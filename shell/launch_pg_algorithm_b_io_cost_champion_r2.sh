#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/20260815_algorithm_b_grid15_io_cost_champion_clean_r2
runner=${project_root}/shell/run_pg_algorithm_b_io_cost_champion_r2_lane.sh
supervisor=${project_root}/shell/run_pg_algorithm_c_cohort_support_supervisor.py
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python

for lane in job tpch xp_cpu xp_io; do
    session=pgb_iochamp_r2_${lane}
    if tmux has-session -t "${session}" 2>/dev/null; then
        echo "tmux session already exists: ${session}" >&2
        exit 1
    fi
done
for lane in job tpch xp_cpu xp_io; do
    tmux new-session -d -s "pgb_iochamp_r2_${lane}" -c "${project_root}" \
        "exec ${runner} ${lane}"
done
tmux new-session -d -s pgb_iochamp_r2_support -c "${project_root}" \
    "exec ${python_bin} -B -u ${supervisor} --campaign-root ${campaign_root} --cohort-id 20260815_algorithm_b_io_cost_champion_candidate_r2 --cohort-size 4 --timeout-s 1800 --state-file-name algorithm_b_state.json --history-file-name algorithm_b.jsonl --controller-script-name run_pg_algorithm_b_scene.py >> ${campaign_root}/cohort_support/supervisor.log 2>&1"

tmux list-sessions -F '#S' | sort | grep '^pgb_iochamp_r2_'
