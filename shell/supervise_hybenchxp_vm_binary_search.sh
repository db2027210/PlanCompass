#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 5 || $# -gt 6 ]]; then
    echo "usage: $0 CONFIG OUTPUT_DIR TARGET PGHOST CONTROLLER_LOG [TUNER_SCRIPT]" >&2
    exit 2
fi

CONFIG=$1
OUTPUT_DIR=$2
TARGET=$3
DB_HOST=$4
CONTROLLER_LOG=$5
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TUNER_ROOT=${BINARY_PLAN_RESOURCE_TUNER_ROOT:-$ROOT_DIR}
TUNER_SCRIPT=${6:-$ROOT_DIR/binary_plan_resource_tuner.py}
STAGE_LOG="$OUTPUT_DIR/${TARGET}_binary_search_hybenchxp.jsonl"
FINAL_JSON="$OUTPUT_DIR/final_for_${TARGET}.json"
TERMINAL_STOP_REASONS=${BINARY_PLAN_TERMINAL_STOP_REASONS:-epsilon_converged}

mkdir -p "$OUTPUT_DIR"
attempt=0
while true; do
    attempt=$((attempt + 1))
    resume_args=()
    if [[ -s "$STAGE_LOG" ]] && jq -s -e 'any(.[]; .event == "baseline_validation" and .status == "ok")' "$STAGE_LOG" >/dev/null 2>&1; then
        resume_args=(--resume)
    fi
    printf '[supervisor] target=%s attempt=%d resume=%s started=%s\n' \
        "$TARGET" "$attempt" "${resume_args[*]:-no}" "$(date --iso-8601=seconds)" | tee -a "$CONTROLLER_LOG"
    set +e
    env \
        PGHOST="$DB_HOST" \
        PGPORT=25432 \
        PGUSER=postgres \
        PGPASSWORD=CHANGE_ME \
        PGDATABASE=hybench_sf10x \
        BINARY_PLAN_RESOURCE_TUNER_ROOT="$TUNER_ROOT" \
        PYTHONPATH="$TUNER_ROOT:$TUNER_ROOT/shell${PYTHONPATH:+:$PYTHONPATH}" \
        python3 -u "$TUNER_SCRIPT" \
        --config "$CONFIG" \
        --output-dir "$OUTPUT_DIR" \
        binary-search \
        --target "$TARGET" \
        "${resume_args[@]}" 2>&1 | tee -a "$CONTROLLER_LOG"
    rc=${PIPESTATUS[0]}
    set -e
    if [[ -s "$FINAL_JSON" ]] && jq -e --arg reasons "$TERMINAL_STOP_REASONS" \
        '.stop_reason as $reason
         | (($reasons | split(" ") | index($reason)) != null)
         and ($reason != "epsilon_converged" or .converged == true)' \
        "$FINAL_JSON" >/dev/null; then
        printf '[supervisor] target=%s complete rc=%d finished=%s\n' \
            "$TARGET" "$rc" "$(date --iso-8601=seconds)" | tee -a "$CONTROLLER_LOG"
        exit 0
    fi
    printf '[supervisor] target=%s incomplete rc=%d; retrying in 10s\n' "$TARGET" "$rc" | tee -a "$CONTROLLER_LOG"
    sleep 10
done
