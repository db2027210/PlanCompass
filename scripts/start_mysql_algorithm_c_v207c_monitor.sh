#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/anonymous/program/resource_adaptive_mysql
RUN_DIR="$ROOT/history/mysql_four_scene_20260809/c_v207c_pg_boundary_fallback_four_scene_20260929"
PID_FILE="$RUN_DIR/completion_monitor.pid"
LOG_FILE="$RUN_DIR/completion_monitor.stdout.log"
PYTHON=/home/anonymous/miniconda3/envs/py313/bin/python
MONITOR="$ROOT/scripts/monitor_mysql_algorithm_c_v207c_completion.py"

if [[ -s "$PID_FILE" ]]; then
    pid=$(tr -d '[:space:]' <"$PID_FILE")
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
        cmdline=$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)
        if [[ "$cmdline" == *"$MONITOR"* ]]; then
            echo "ALREADY_RUNNING pid=$pid log=$LOG_FILE"
            exit 0
        fi
    fi
fi

nohup setsid "$PYTHON" -u "$MONITOR" >>"$LOG_FILE" 2>&1 </dev/null &
pid=$!
printf '%s\n' "$pid" >"$PID_FILE"
sleep 2
kill -0 "$pid"
echo "STARTED pid=$pid log=$LOG_FILE"
