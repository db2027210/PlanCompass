#!/usr/bin/env bash
set -euo pipefail

BUNDLE=/home/anonymous/program/resource_adaptive/pg4_relaunch_20260727
RUN_DIR="$BUNDLE/run"
PID_FILE="$RUN_DIR/monitor_5min.pid"
LOG_FILE="$RUN_DIR/monitor_5min.log"
PYTHON=/home/anonymous/miniconda3/envs/py313/bin/python

mkdir -p "$RUN_DIR"
if [[ -s "$PID_FILE" ]]; then
    pid=$(tr -d '[:space:]' <"$PID_FILE")
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
        cmdline=$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)
        if [[ "$cmdline" == *"$BUNDLE/monitor_four_lanes_5min.py"* ]]; then
            echo "ALREADY_RUNNING pid=$pid log=$LOG_FILE"
            exit 0
        fi
    fi
    mv "$PID_FILE" "$PID_FILE.stale.$(date +%Y%m%dT%H%M%S)"
fi

nohup setsid "$PYTHON" -u "$BUNDLE/monitor_four_lanes_5min.py" \
    --follow --interval 300 >>"$LOG_FILE" 2>&1 </dev/null &
pid=$!
printf '%s\n' "$pid" >"$PID_FILE"
sleep 2
kill -0 "$pid"
echo "STARTED pid=$pid log=$LOG_FILE"
