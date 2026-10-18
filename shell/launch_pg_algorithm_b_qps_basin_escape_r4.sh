#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/20260822_algorithm_b_last_accepted_qps_basin_escape_clean_r4
runner=${project_root}/shell/run_pg_algorithm_bc_last_accepted_20260822_lane.sh
supervisor=${project_root}/shell/run_pg_algorithm_c_cohort_support_supervisor.py
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
cohort_prefix=20260822_algorithm_b_qps_basin_escape_r4

for lane in job tpch xp_cpu xp_io; do
    session=pgb_qpsbasin_r4_${lane}
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

export PG_ALGORITHM_BC_B_ROOT=${campaign_root}
export PG_ALGORITHM_BC_B_COHORT_PREFIX=${cohort_prefix}
for lane in job tpch xp_cpu xp_io; do
    tmux new-session -d -s "pgb_qpsbasin_r4_${lane}" \
        -c "${project_root}" "exec ${runner} B ${lane}"
done
tmux new-session -d -s pgb_qpsbasin_r4_support -c "${project_root}" \
    "exec ${python_bin} -B -u ${supervisor} --campaign-root ${campaign_root} --cohort-id ${cohort_prefix}_candidate --cohort-size 4 --timeout-s 1800 --terminal-wait-timeout-s 86400 --state-file-name algorithm_b_state.json --history-file-name algorithm_b.jsonl --controller-script-name run_pg_algorithm_b_scene.py >> ${campaign_root}/cohort_support/supervisor.log 2>&1"

tmux list-sessions -F '#S' | sort | grep '^pgb_qpsbasin_r4_'
