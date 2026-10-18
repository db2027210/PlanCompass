#!/usr/bin/env bash
set -euo pipefail

root=/home/anonymous/program/resource_adaptive
config="$root/config/formal/pg4_four_experiments_live_20260727.json"
output="$root/history/pg4_four_experiments_live_20260727"
pidfile="$output/monitor.pid"
log="$output/monitor.log"
unit=pg4-four-experiment-monitor.service

mkdir -p "$output"
if systemctl --user is-active --quiet "$unit"; then
    echo "monitor already active: unit=$unit"
    exit 0
fi

systemd-run --user --unit="${unit%.service}" --collect \
    --property=StandardOutput="append:$log" \
    --property=StandardError="append:$log" \
    /usr/bin/python3 -u "$root/shell/monitor_pg4_four_experiments.py" \
    --config "$config" --watch --interval 300 >/dev/null
pid="$(systemctl --user show "$unit" -p MainPID --value)"
printf '%s\n' "$pid" >"$pidfile"
echo "monitor_pid=$pid unit=$unit latest=$output/latest.md log=$log"
