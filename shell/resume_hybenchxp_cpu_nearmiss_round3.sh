#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${1:-/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/hyxp_cpu_formal_20260709_131733}"
CONFIG="${BINARY_PLAN_TUNING_HYBENCHXP_CONFIG:-${ROOT_DIR}/config/binary_plan_tuning_hybenchxp_cpu_nearmiss_resume_20260709.json}"
PYTHON_BIN="${PYTHON:-/home/anonymous/miniconda3/envs/resource_adaptive/bin/python}"

export BINARY_PLAN_TUNING_HYBENCHXP_SKIP_PREPARE="${BINARY_PLAN_TUNING_HYBENCHXP_SKIP_PREPARE:-1}"
export BINARY_PLAN_TUNING_HYBENCHXP_CONFIG="${CONFIG}"
export PYTHON="${PYTHON_BIN}"

exec "${ROOT_DIR}/shell/resume_hybenchxp_cpu_current_run.sh" "${RUN_DIR}"
