#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=${PG_BC_NONE_CAMPAIGN_ROOT:-/disk1/resource_adaptive/pg_algorithm_bc_none_20260825/20260825_native_xp_x1x1_none_current_r1}
runner=${project_root}/shell/run_pg_algorithm_bc_none_lane.sh
tmux_socket=${PG_BC_NONE_TMUX_SOCKET:-pg_bc_none_20260825_r1}
baseline_cohort_id=${PG_BC_NONE_BASELINE_COHORT_ID:-20260825_pg_bc_none_current_r1_baseline}

for lane in algorithm_b_none algorithm_c_none; do
    if tmux -L "${tmux_socket}" has-session -t "${lane}" 2>/dev/null; then
        echo "controller session already exists: ${lane}" >&2
        exit 1
    fi
    if [[ -e "${campaign_root}/${lane}/algorithm_b_state.json" || \
          -e "${campaign_root}/${lane}/algorithm_c_state.json" ]]; then
        echo "formal state already exists: ${campaign_root}/${lane}" >&2
        exit 1
    fi
done

for lane in algorithm_b_none algorithm_c_none; do
    command="exec env PG_BC_NONE_CAMPAIGN_ROOT=${campaign_root} PG_BC_NONE_BASELINE_COHORT_ID=${baseline_cohort_id} ${runner} ${lane}"
    tmux -L "${tmux_socket}" new-session -d -s "${lane}" \
        -c "${project_root}" "${command}"
done
tmux -L "${tmux_socket}" set-option -g remain-on-exit on

sleep 2
for lane in algorithm_b_none algorithm_c_none; do
    pane_pid=$(tmux -L "${tmux_socket}" display-message -p \
        -t "${lane}:0.0" '#{pane_pid}')
    pane_dead=$(tmux -L "${tmux_socket}" display-message -p \
        -t "${lane}:0.0" '#{pane_dead}')
    printf '%s\n' "${pane_pid}" > "${campaign_root}/${lane}/controller.pid"
    printf '%s pane_pid=%s pane_dead=%s\n' "${lane}" "${pane_pid}" "${pane_dead}"
done
