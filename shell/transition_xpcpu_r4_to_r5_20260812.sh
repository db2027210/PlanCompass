#!/usr/bin/env bash
set -euo pipefail

readonly OLD_PID="3667838"
readonly PYTHON_BIN="/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
readonly LAUNCHER="/home/anonymous/program/resource_adaptive/shell/launch_pg_algorithm_c_four_experience_sgd_20260809.py"
readonly NEW_TAG="20260812_algorithm_c_xpcpu_calrelease_formalgatefix_clean_r5"

while kill -0 "${OLD_PID}" 2>/dev/null; do
    sleep 5
done

"${PYTHON_BIN}" -B "${LAUNCHER}" prepare --tag "${NEW_TAG}" --scene xp_cpu
exec "${PYTHON_BIN}" -B -u "${LAUNCHER}" run --tag "${NEW_TAG}" --scene xp_cpu
