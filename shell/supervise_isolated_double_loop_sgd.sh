#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
    echo "usage: $0 OUTPUT_BASE SCENARIO RUNNER_ARG..." >&2
    exit 2
fi

OUTPUT_BASE=$1
SCENARIO=$2
shift 2

case "$SCENARIO" in
    cpu|memory|io|none) ;;
    *)
        echo "unsupported scenario: $SCENARIO" >&2
        exit 2
        ;;
esac

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON_BIN=${DOUBLE_LOOP_PYTHON:-/home/anonymous/miniconda3/envs/resource_adaptive/bin/python}
RUNNER=${DOUBLE_LOOP_RUNNER:-$ROOT_DIR/shell/double_loop_isolated_entry.py}
RETRY_DELAY_SEC=${DOUBLE_LOOP_RETRY_DELAY_SEC:-15}
mkdir -p "$OUTPUT_BASE"

exec 9>"$OUTPUT_BASE/.supervisor.lock"
if ! flock -n 9; then
    echo "another isolated double-loop supervisor already owns $OUTPUT_BASE" >&2
    exit 73
fi

SUPERVISOR_LOG="$OUTPUT_BASE/supervisor.log"
child_pid=""

log() {
    printf '[double-loop-supervisor] %s scenario=%s %s\n' \
        "$(date --iso-8601=seconds)" "$SCENARIO" "$*" | tee -a "$SUPERVISOR_LOG"
}

stop_child() {
    if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
        kill -TERM -- "-$child_pid" 2>/dev/null || true
        sleep 2
        kill -KILL -- "-$child_pid" 2>/dev/null || true
    fi
}
trap 'stop_child; exit 130' INT TERM

attempt=0
while true; do
    attempt=$((attempt + 1))
    stamp=$(date +%Y%m%d_%H%M%S)
    attempt_dir="$OUTPUT_BASE/attempt_$(printf '%02d' "$attempt")_$stamp"
    mkdir -p "$attempt_dir"
    log "attempt=$attempt output=$attempt_dir starting"

    set +e
    setsid "$PYTHON_BIN" -u "$RUNNER" \
        "$@" \
        --scenarios "$SCENARIO" \
        --output-dir "$attempt_dir" \
        >"$attempt_dir/controller.log" 2>&1 &
    child_pid=$!
    printf '%s\n' "$child_pid" >"$OUTPUT_BASE/current_child.pid"
    wait "$child_pid"
    rc=$?
    set -e
    child_pid=""

    summary="$attempt_dir/$SCENARIO/scenario_summary.json"
    if [[ -s "$summary" ]]; then
        if jq -e '(.status == "ok" or .status == "completed") and .successful == true' \
            "$summary" >/dev/null; then
            log "attempt=$attempt rc=$rc completed_successfully summary=$summary"
            printf '%s\n' "$attempt_dir" >"$OUTPUT_BASE/completed_attempt.txt"
            exit 0
        fi
        log "attempt=$attempt rc=$rc completed_without_goal; stopping_for_diagnosis summary=$summary"
        printf '%s\n' "$attempt_dir" >"$OUTPUT_BASE/diagnostic_attempt.txt"
        exit 2
    fi

    log "attempt=$attempt rc=$rc interrupted_without_summary; retrying_fresh_in=${RETRY_DELAY_SEC}s"
    sleep "$RETRY_DELAY_SEC"
done
