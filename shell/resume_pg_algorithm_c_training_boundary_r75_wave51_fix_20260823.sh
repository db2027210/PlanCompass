#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260823_algorithm_c_training_boundary_clean_r75
runner=${project_root}/shell/run_pg_algorithm_bc_last_accepted_20260822_lane.sh
supervisor=${project_root}/shell/run_pg_algorithm_c_cohort_support_supervisor.py
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
cohort_prefix=20260823_algorithm_c_training_boundary_r75
session_prefix=pgc_trainbound_r75

for suffix in tpch xp_cpu xp_io support; do
    session=${session_prefix}_${suffix}
    if tmux has-session -t "${session}" 2>/dev/null; then
        echo "tmux session already exists: ${session}" >&2
        exit 1
    fi
done

for scene in tpch_memory xp_cpu xp_io; do
    test -f "${campaign_root}/${scene}/algorithm_c_state.json"
    test -f "${campaign_root}/${scene}/algorithm_c.jsonl"
    test "$(jq -r '.phase' "${campaign_root}/${scene}/algorithm_c_state.json")" = "stopped_by_signal_clean"
    test "$(jq -r '.signal_stop_clean_receipt.passed' "${campaign_root}/${scene}/algorithm_c_state.json")" = "true"
done

for lane in tpch xp_cpu xp_io; do
    tmux new-session -d -s "${session_prefix}_${lane}" \
        -c "${project_root}" \
        "export PG_ALGORITHM_BC_C_ROOT=${campaign_root} PG_ALGORITHM_BC_C_COHORT_PREFIX=${cohort_prefix} BONECT_SHARED_SSD_COORDINATION_ENABLED=0 BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE=none BONECT_ALGORITHM_C_RESUME_LEGACY_STOPPED_ROUND=1; exec ${runner} C ${lane}"
done
tmux new-session -d -s "${session_prefix}_support" \
    -c "${project_root}" \
    "export BONECT_SHARED_SSD_COORDINATION_ENABLED=0 BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE=none; exec ${python_bin} -B -u ${supervisor} --campaign-root ${campaign_root} --cohort-id ${cohort_prefix}_candidate --cohort-size 4 --timeout-s 1800 --terminal-wait-timeout-s 86400 >> ${campaign_root}/cohort_support/supervisor.log 2>&1"

tmux list-sessions -F '#S' | sort | grep "^${session_prefix}_"
