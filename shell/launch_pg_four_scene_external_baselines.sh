#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
pipeline=${project_root}/shell/run_pg_four_scene_external_baselines.sh
baseline_root=${PG_EXTERNAL_BASELINE_ROOT:-/disk1/resource_adaptive/pg_external_four_scene_baselines_20260825/20260825_gptuner_aqetuner_four_r1}
tmux_socket=${PG_EXTERNAL_BASELINE_TMUX_SOCKET:-pg_external_four_20260825_r1}
session=${PG_EXTERNAL_BASELINE_TMUX_SESSION:-gptuner_then_aqetuner_four}

mkdir -p "${baseline_root}"
if tmux -L "${tmux_socket}" has-session -t "${session}" 2>/dev/null; then
    echo "external baseline session already exists: ${tmux_socket}/${session}" >&2
    exit 1
fi

command="exec ${pipeline} >> ${baseline_root}/pipeline.log 2>&1"
tmux -L "${tmux_socket}" new-session -d -s "${session}" \
    -c "${project_root}" "${command}"
tmux -L "${tmux_socket}" set-option -g remain-on-exit on
sleep 2

pane_pid=$(tmux -L "${tmux_socket}" display-message -p \
    -t "${session}:0.0" '#{pane_pid}')
pane_dead=$(tmux -L "${tmux_socket}" display-message -p \
    -t "${session}:0.0" '#{pane_dead}')
printf '%s\n' "${pane_pid}" > "${baseline_root}/pipeline.pid"
printf 'session=%s pane_pid=%s pane_dead=%s log=%s\n' \
    "${session}" "${pane_pid}" "${pane_dead}" "${baseline_root}/pipeline.log"
