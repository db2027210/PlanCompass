#!/usr/bin/env bash
set -euo pipefail

LANE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/hybench_ap_node01
SOURCE_ATTEMPT="$LANE/results/experience_outer/attempt_16_single_score_restart_20260724"
INITIAL_REPLAY="$SOURCE_ATTEMPT/generation_05/replay"
OUTPUT="$LANE/results/experience_outer/attempt_17_continue_from_accepted4_20260724"
LOG="$LANE/logs/attempt_17_continue_from_accepted4_20260724.log"
PID_FILE="$LANE/attempt_17_continue_from_accepted4_20260724.pid"
EXPECTED_STATE_SHA256=60b8e1062d2935c68fe7c366f48be810d4e579067e12f1eefbc2e646249bc3a1
EXPECTED_SUPERVISOR_SHA256=d3cb141cb5fdd2ecb4302aece323a0faee00c99b878252a0cad8eb72478d22c0

test -f "$INITIAL_REPLAY/next_incumbent_state.json"
test "$(sha256sum "$INITIAL_REPLAY/next_incumbent_state.json" | awk '{print $1}')" = "$EXPECTED_STATE_SHA256"
test "$(sha256sum "$LANE/run_ap_tsgd_outer_supervisor.py" | awk '{print $1}')" = "$EXPECTED_SUPERVISOR_SHA256"
test "$(jq -r '.accepted_shrinks' "$INITIAL_REPLAY/next_incumbent_state.json")" = 4
test "$(jq -r '.status' "$INITIAL_REPLAY/next_incumbent_state.json")" = accepted_incumbent_state
test ! -e "$OUTPUT"

if pgrep -f "[r]un_ap_tsgd_outer_supervisor.py.*attempt_17_continue_from_accepted4_20260724" >/dev/null; then
    printf 'attempt17 continuation is already active\n' >&2
    exit 75
fi
if docker ps --format '{{.Names}}' | grep -Eq 'pg5hya|hybench_ap'; then
    printf 'HyBench AP lane still has an active container\n' >&2
    exit 75
fi

nohup setsid env -u PYTHONPATH \
    /usr/bin/python3 -u \
    "$LANE/run_ap_tsgd_outer_supervisor.py" \
    --lane-root "$LANE" \
    --initial-replay-root "$INITIAL_REPLAY" \
    --output-root "$OUTPUT" \
    --max-accepted-shrinks 10 \
    --deadline-hours 12 \
    >"$LOG" 2>&1 </dev/null &
runner_pid=$!
printf '%s\n' "$runner_pid" >"$PID_FILE"
printf 'pid=%s output=%s accepted_checkpoint_sha256=%s\n' \
    "$runner_pid" "$OUTPUT" "$EXPECTED_STATE_SHA256"
