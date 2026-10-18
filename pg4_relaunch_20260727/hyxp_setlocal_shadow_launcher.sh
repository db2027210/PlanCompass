#!/usr/bin/env bash
set -euo pipefail

: "${HYXP_SETLOCAL_DRIVER_JAR:?}"
: "${HYXP_HYBENCH_ROOT:?}"
: "${HYXP_SETLOCAL_PARAMETERS:?}"
: "${HYXP_SETLOCAL_AUDIT_PATH:?}"
: "${HYXP_SETLOCAL_RUN_ID:?}"
: "${HYXP_SETLOCAL_PHASE:?}"

cd "$HYXP_HYBENCH_ROOT"

exec java \
    -cp "$HYXP_SETLOCAL_DRIVER_JAR:$HYXP_HYBENCH_ROOT/lib/*:$HYXP_HYBENCH_ROOT" \
    com.hybench.HyBench "$@"
