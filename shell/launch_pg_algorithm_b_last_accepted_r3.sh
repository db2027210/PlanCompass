#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/20260822_algorithm_b_last_accepted_qps_clean_r3
runner=${project_root}/shell/run_pg_algorithm_bc_last_accepted_20260822_lane.sh
supervisor=${project_root}/shell/run_pg_algorithm_c_cohort_support_supervisor.py
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python

for lane in job tpch xp_cpu xp_io; do
    session=pgb_qpsprev_r3_${lane}
    if tmux has-session -t "${session}" 2>/dev/null; then
        echo "tmux session already exists: ${session}" >&2
        exit 1
    fi
done

for scene in job_memory tpch_memory xp_cpu xp_io; do
    test -f "${campaign_root}/${scene}/optimizer_config.json"
    test ! -e "${campaign_root}/${scene}/algorithm_b_state.json"
    test ! -e "${campaign_root}/${scene}/algorithm_b.jsonl"
done

for lane in job tpch xp_cpu xp_io; do
    tmux new-session -d -s "pgb_qpsprev_r3_${lane}" -c "${project_root}" \
        "exec ${runner} B ${lane}"
done
tmux new-session -d -s pgb_qpsprev_r3_support -c "${project_root}" \
    "exec ${python_bin} -B -u ${supervisor} --campaign-root ${campaign_root} --cohort-id 20260822_algorithm_b_last_accepted_r3_candidate --cohort-size 4 --timeout-s 1800 --state-file-name algorithm_b_state.json --history-file-name algorithm_b.jsonl --controller-script-name run_pg_algorithm_b_scene.py >> ${campaign_root}/cohort_support/supervisor.log 2>&1"

tmux list-sessions -F '#S' | sort | grep '^pgb_qpsprev_r3_'
