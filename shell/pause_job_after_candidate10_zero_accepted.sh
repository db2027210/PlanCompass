#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 3 ]]; then
    echo "usage: $0 ATTEMPT_ROOT OPTIMIZER_PID HISTORY_PATH" >&2
    exit 2
fi

attempt_root=$1
optimizer_pid=$2
history_path=$3
accepted_counter_path="$attempt_root/formal_search_accepted_counter.txt"
marker_path="$attempt_root/paused_at_candidate10_zero_accepted.json"

while kill -0 "$optimizer_pid" 2>/dev/null; do
    accepted_count=$(tr -d '\n' < "$accepted_counter_path")
    event_line=$(
        tail -n 8 "$history_path" \
            | grep -F '"event": "experience_table_gradient_initial_real_replay_complete"' \
            | tail -n 1 \
            || true
    )
    if [[ "$accepted_count" == "0" \
        && "$event_line" == *'"validated_candidate_count": 10'* \
        && "$event_line" == *'"eligible_count": 0'* ]]; then
        timestamp=$(date -Is)
        kill -STOP "$optimizer_pid"
        printf '{"timestamp":"%s","action":"SIGSTOP","optimizer_pid":%s,"reason":"candidate10_complete_eligible0_accepted0","history_path":"%s"}\n' \
            "$timestamp" "$optimizer_pid" "$history_path" > "$marker_path"
        printf '%s paused optimizer_pid=%s\n' "$timestamp" "$optimizer_pid"
        exit 0
    fi
    sleep 0.2
done

printf '%s optimizer_exited_before_guard\n' "$(date -Is)"
