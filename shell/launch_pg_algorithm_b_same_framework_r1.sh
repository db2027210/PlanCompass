#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/20260814_algorithm_b_grid15_same_framework_r1
runner=${project_root}/shell/run_pg_algorithm_b_same_framework_r1_lane.sh
supervisor=${project_root}/shell/run_pg_algorithm_c_cohort_support_supervisor.py
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python

for lane in job tpch xp_cpu xp_io; do
    session=pgb_grid_r1_${lane}
    if tmux has-session -t "${session}" 2>/dev/null; then
        echo "tmux session already exists: ${session}" >&2
        exit 1
    fi
done

for lane in job tpch xp_cpu xp_io; do
    tmux new-session -d -s "pgb_grid_r1_${lane}" -c "${project_root}" \
        "exec ${runner} ${lane}"
done
tmux new-session -d -s pgb_grid_r1_support -c "${project_root}" \
    "exec ${python_bin} -B -u ${supervisor} --campaign-root ${campaign_root} --cohort-id 20260814T1030_algorithm_b_grid_candidate_r1 --cohort-size 4 --timeout-s 1800 --state-file-name algorithm_b_state.json --history-file-name algorithm_b.jsonl --controller-script-name run_pg_algorithm_b_scene.py >> ${campaign_root}/cohort_support/supervisor.log 2>&1"

tmux list-sessions -F '#S' | sort | grep '^pgb_grid_r1_'
