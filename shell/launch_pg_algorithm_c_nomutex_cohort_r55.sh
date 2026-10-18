#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260813_algorithm_c_identity_directional_cohort_clean_r55
runner=${project_root}/shell/run_pg_algorithm_c_nomutex_cohort_r55_lane.sh
supervisor=${project_root}/shell/run_pg_algorithm_c_cohort_support_supervisor.py
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python

mkdir -p "${campaign_root}/cohort_support"

for lane in job tpch xp_cpu xp_io; do
    session=pgc_nomutex_r55_${lane}
    if tmux has-session -t "${session}" 2>/dev/null; then
        echo "tmux session already exists: ${session}" >&2
        exit 1
    fi
done
if tmux has-session -t pgc_nomutex_r55_support 2>/dev/null; then
    echo "tmux session already exists: pgc_nomutex_r55_support" >&2
    exit 1
fi

for lane in job tpch xp_cpu xp_io; do
    session=pgc_nomutex_r55_${lane}
    tmux new-session -d -s "${session}" -c "${project_root}" \
        "exec ${runner} ${lane}"
done

tmux new-session -d -s pgc_nomutex_r55_support -c "${project_root}" \
    "exec ${python_bin} -B -u ${supervisor} --campaign-root ${campaign_root} --cohort-id 20260813T0840_nomutex_candidate_r55 --cohort-size 4 --timeout-s 1800 >> ${campaign_root}/cohort_support/supervisor.log 2>&1"

tmux list-sessions -F '#S' | sort | grep '^pgc_nomutex_r55_'
