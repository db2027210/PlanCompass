#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/20260823_algorithm_b_training_boundary_clean_r12
runner=${project_root}/shell/run_pg_algorithm_bc_last_accepted_20260822_lane.sh
supervisor=${project_root}/shell/run_pg_algorithm_c_cohort_support_supervisor.py
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
cohort_prefix=20260823_algorithm_b_training_boundary_r12
session_prefix=pgb_trainbound_r12

for suffix in job tpch xp_cpu xp_io support; do
    session=${session_prefix}_${suffix}
    if tmux has-session -t "${session}" 2>/dev/null; then
        echo "tmux session already exists: ${session}" >&2
        exit 1
    fi
done

test -f "${campaign_root}/fresh_campaign_manifest.json"
for scene in job_memory tpch_memory xp_cpu xp_io; do
    test -f "${campaign_root}/${scene}/optimizer_config.json"
    test -f "${campaign_root}/${scene}/experience_table_T_abc_c_holdout_admitted.json"
    test ! -e "${campaign_root}/${scene}/algorithm_b_state.json"
    test ! -e "${campaign_root}/${scene}/algorithm_b.jsonl"
done

for lane in job tpch xp_cpu xp_io; do
    tmux new-session -d -s "${session_prefix}_${lane}" \
        -c "${project_root}" \
        "exec env PG_ALGORITHM_BC_B_ROOT=${campaign_root} PG_ALGORITHM_BC_B_COHORT_PREFIX=${cohort_prefix} ${runner} B ${lane}"
done
tmux new-session -d -s "${session_prefix}_support" \
    -c "${project_root}" \
    "exec ${python_bin} -B -u ${supervisor} --campaign-root ${campaign_root} --cohort-id ${cohort_prefix}_candidate --cohort-size 4 --timeout-s 1800 --terminal-wait-timeout-s 86400 --state-file-name algorithm_b_state.json --history-file-name algorithm_b.jsonl --controller-script-name run_pg_algorithm_b_scene.py >> ${campaign_root}/cohort_support/supervisor.log 2>&1"

tmux list-sessions -F '#S' | sort | grep "^${session_prefix}_"
