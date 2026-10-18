#!/usr/bin/env bash
set -euo pipefail

FORMAL_PID="${FORMAL_PID:-2995102}"
ROOT_DIR="/home/anonymous/program/resource_adaptive"
CONFIG_PATH="${ROOT_DIR}/config/binary_plan_tuning_hybenchxp_none_reconstruct_seqagg_20260709.json"
RUN_ROOT="/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs"
PYTHON_BIN="/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"

cd "${ROOT_DIR}"

while ps -p "${FORMAL_PID}" >/dev/null 2>&1; do
    date "+%F %T CST waiting formal pid ${FORMAL_PID}"
    sleep 60
done

date "+%F %T CST formal pid ${FORMAL_PID} exited; sleeping 120s before baseline"
sleep 120

TS="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${RUN_ROOT}/hyxp_none_reconstruct_seqagg_protected_baseline_${TS}"

date "+%F %T CST starting seqagg reconstruction baseline-only: ${OUT_DIR}"
BINARY_PLAN_TUNING_HYBENCHXP_SKIP_PREPARE=1 "${PYTHON_BIN}" \
    binary_plan_resource_tuner.py \
    --config "${CONFIG_PATH}" \
    --output-dir "${OUT_DIR}" \
    binary-search \
    --target none \
    --baseline-only

date "+%F %T CST seqagg reconstruction baseline-only finished: ${OUT_DIR}"
