#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMD="${1:-baseline}"
POLL_SEC="${HYBENCHXP_GUARD_WAIT_POLL_SEC:-15}"
MAX_WAIT_SEC="${HYBENCHXP_GUARD_WAIT_MAX_SEC:-0}"
START_TS="$(date +%s)"
BLOCK_FILE="${HYBENCHXP_RUNXP_BLOCK_FILE:-/tmp/bonect_block_external_runxp}"
ALLOW_STALE_BLOCK="${HYBENCHXP_ALLOW_STALE_BLOCK:-0}"
RESPECT_EXTERNAL_GUARD="${HYBENCHXP_RESPECT_EXTERNAL_GUARD:-0}"

has_process_blocker() {
  ps -eo cmd= | grep -E 'bonect_external_xp_guard.py|HyBench .* -t runap|hybench -t runap|run_pg_benchmark_bottleneck_matrix.py|parameter_optimizer.py' \
    | grep -v -E 'grep -E|wait_hybenchxp_guard_then_run.sh' >/dev/null
}

if [[ "${RESPECT_EXTERNAL_GUARD}" != "1" ]]; then
  export BINARY_PLAN_TUNING_HYBENCHXP_IGNORE_EXTERNAL_GUARD=1
  echo "[hybenchxp-guard-wait] external guard wait disabled; running ${CMD}" >&2
  exec "${ROOT_DIR}/shell/run_binary_plan_tuning_hybenchxp.sh" "${CMD}"
fi

while true; do
  now="$(date +%s)"
  waited=$((now - START_TS))
  process_blocked=0
  block_file_present=0
  if has_process_blocker; then
    process_blocked=1
  fi
  if [[ -f "${BLOCK_FILE}" ]]; then
    block_file_present=1
  fi
  if [[ "${process_blocked}" == "0" && ( "${block_file_present}" == "0" || "${ALLOW_STALE_BLOCK}" == "1" ) ]]; then
    if [[ "${block_file_present}" == "1" && "${ALLOW_STALE_BLOCK}" == "1" ]]; then
      export BINARY_PLAN_TUNING_HYBENCHXP_IGNORE_EXTERNAL_GUARD=1
      echo "[hybenchxp-guard-wait] treating stale block file as non-blocking: ${BLOCK_FILE}" >&2
    fi
    break
  fi
  echo "[hybenchxp-guard-wait] waited=${waited}s process_blocked=${process_blocked} block_file=${block_file_present} cmd=${CMD}" >&2
  if (( MAX_WAIT_SEC > 0 && waited >= MAX_WAIT_SEC )); then
    echo "[hybenchxp-guard-wait] timeout after ${waited}s" >&2
    exit 124
  fi
  sleep "${POLL_SEC}"
done

echo "[hybenchxp-guard-wait] guard clear; running ${CMD}" >&2
exec "${ROOT_DIR}/shell/run_binary_plan_tuning_hybenchxp.sh" "${CMD}"
