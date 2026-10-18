#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260815_algorithm_c_io_cost_champion_clean_r66
runner=${project_root}/shell/run_pg_algorithm_c_io_cost_champion_r66_lane.sh
supervisor=${project_root}/shell/run_pg_algorithm_c_cohort_support_supervisor.py
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python

mkdir -p "${campaign_root}/cohort_support"
for lane in job tpch xp_cpu xp_io; do
    session=pgc_iochamp_r66_${lane}
    if tmux has-session -t "${session}" 2>/dev/null; then
        echo "tmux session already exists: ${session}" >&2
        exit 1
    fi
done

for lane in job tpch xp_cpu xp_io; do
    tmux new-session -d -s "pgc_iochamp_r66_${lane}" -c "${project_root}" \
        "exec ${runner} ${lane}"
done
tmux new-session -d -s pgc_iochamp_r66_support -c "${project_root}" \
    "exec ${python_bin} -B -u ${supervisor} --campaign-root ${campaign_root} --cohort-id 20260815T0220_io_cost_champion_candidate_r66 --cohort-size 4 --timeout-s 1800 >> ${campaign_root}/cohort_support/supervisor.log 2>&1"

tmux list-sessions -F '#S' | sort | grep '^pgc_iochamp_r66_'
