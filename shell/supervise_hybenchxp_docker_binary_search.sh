#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 || $# -gt 5 ]]; then
    echo "usage: $0 CONFIG OUTPUT_DIR TARGET CONTROLLER_LOG [TUNER_SCRIPT]" >&2
    exit 2
fi

CONFIG=$1
OUTPUT_DIR=$2
TARGET=$3
CONTROLLER_LOG=$4
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TUNER_SCRIPT=${5:-$ROOT_DIR/binary_plan_resource_tuner.py}
STAGE_LOG="$OUTPUT_DIR/${TARGET}_binary_search_hybenchxp.jsonl"
FINAL_JSON="$OUTPUT_DIR/final_for_${TARGET}.json"
mkdir -p "$OUTPUT_DIR"
attempt=0
while true; do
    attempt=$((attempt + 1))
    resume_args=()
    if [[ -s "$STAGE_LOG" ]] && jq -s -e \
        'any(.[]; .event == "baseline_validation" and .status == "ok")' \
        "$STAGE_LOG" >/dev/null 2>&1; then
        resume_args=(--resume)
    fi
    printf '[docker-supervisor] target=%s attempt=%d resume=%s started=%s\n' \
        "$TARGET" "$attempt" "${resume_args[*]:-no}" "$(date --iso-8601=seconds)" \
        | tee -a "$CONTROLLER_LOG"

    set +e
    env \
        BINARY_PLAN_RESOURCE_TUNER_ROOT="$ROOT_DIR" \
        PYTHONPATH="$ROOT_DIR:$ROOT_DIR/shell${PYTHONPATH:+:$PYTHONPATH}" \
        python3 -u "$TUNER_SCRIPT" \
        --config "$CONFIG" \
        --output-dir "$OUTPUT_DIR" \
        binary-search \
        --target "$TARGET" \
        "${resume_args[@]}" 2>&1 | tee -a "$CONTROLLER_LOG"
    rc=${PIPESTATUS[0]}
    set -e

    if [[ -s "$FINAL_JSON" ]] && jq -e \
        '.stop_reason == "epsilon_converged" and .converged == true' \
        "$FINAL_JSON" >/dev/null; then
        printf '[docker-supervisor] target=%s complete rc=%d finished=%s\n' \
            "$TARGET" "$rc" "$(date --iso-8601=seconds)" | tee -a "$CONTROLLER_LOG"
        exit 0
    fi
    printf '[docker-supervisor] target=%s incomplete rc=%d; retrying in 10s\n' \
        "$TARGET" "$rc" | tee -a "$CONTROLLER_LOG"
    sleep 10
done
