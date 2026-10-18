#!/usr/bin/env bash
set -euo pipefail

campaign_root=${1:?usage: watch_pg_algorithm_bc_none_tmux.sh CAMPAIGN_ROOT TMUX_SOCKET}
tmux_socket=${2:?usage: watch_pg_algorithm_bc_none_tmux.sh CAMPAIGN_ROOT TMUX_SOCKET}
status_path=${campaign_root}/controller_watchdog.status

while true; do
    temporary=${status_path}.tmp.$$
    failed=0
    {
        date '+timestamp=%F %T'
        for lane in algorithm_b_none algorithm_c_none; do
            pane_dead=$(tmux -L "${tmux_socket}" display-message -p \
                -t "${lane}:0.0" '#{pane_dead}')
            pane_status=$(tmux -L "${tmux_socket}" display-message -p \
                -t "${lane}:0.0" '#{pane_dead_status}')
            pane_pid=$(tmux -L "${tmux_socket}" display-message -p \
                -t "${lane}:0.0" '#{pane_pid}')
            printf '%s pane_dead=%s exit=%s pid=%s\n' \
                "${lane}" "${pane_dead}" "${pane_status}" "${pane_pid}"
            if [[ "${pane_dead}" != 0 ]]; then
                failed=1
            fi
        done
    } > "${temporary}"
    mv "${temporary}" "${status_path}"
    if [[ "${failed}" -ne 0 ]]; then
        exit 4
    fi
    sleep 30
done
