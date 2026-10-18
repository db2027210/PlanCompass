#!/usr/bin/env bash
set -euo pipefail

root=/home/anonymous/program/resource_adaptive
output="$root/history/pg5_five_experiments_live_20260723"
pidfile="$output/monitor.pid"
log="$output/monitor.log"

mkdir -p "$output"
unit=pg5-five-experiment-monitor.service
if systemctl --user is-active --quiet "$unit"; then
    echo "monitor already active: unit=$unit"
    exit 0
fi

systemd-run --user --unit="${unit%.service}" --collect \
    --property=StandardOutput="append:$log" \
    --property=StandardError="append:$log" \
    /usr/bin/python3 -u "$root/shell/monitor_pg5_five_experiments.py" \
    --watch --interval 300 >/dev/null
pid="$(systemctl --user show "$unit" -p MainPID --value)"
printf '%s\n' "$pid" >"$pidfile"
echo "monitor_pid=$pid unit=$unit latest=$output/latest.md log=$log"
