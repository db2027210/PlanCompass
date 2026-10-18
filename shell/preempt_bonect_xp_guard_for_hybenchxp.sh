#!/usr/bin/env bash
set -euo pipefail

# This script is intentionally conservative. It only removes the external
# Bonect XP guard after an explicit confirmation environment variable is set.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BLOCK_FILE="${HYBENCHXP_RUNXP_BLOCK_FILE:-/tmp/bonect_block_external_runxp}"
CONFIRM="${CONFIRM_PREEMPT_BONECT_XP_GUARD:-}"
LOG_DIR="${HYBENCHXP_PREEMPT_LOG_DIR:-/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/preempt_guard}"
LOG_PATH="${LOG_DIR}/preempt_$(date +%Y%m%d_%H%M%S).log"
DRY_RUN=1

if [[ "${CONFIRM}" == "I_UNDERSTAND_THIS_CAN_POLLUTE_BONECT" ]]; then
  DRY_RUN=0
fi

mkdir -p "${LOG_DIR}"

log() {
  printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "${LOG_PATH}" >&2
}

log "root=${ROOT_DIR}"
log "dry_run=${DRY_RUN}"
log "block_file=${BLOCK_FILE}"
log "reason=preempt external Bonect runxp guard for binary_plan_tuning_hybenchxp"

log "matching guard processes:"
ps -eo pid=,ppid=,pgid=,stat=,etime=,cmd= \
  | awk '/bonect_external_xp_guard.py/ && !/awk/ {print}' \
  | tee -a "${LOG_PATH}" >&2 || true

log "matching Bonect benchmark processes still active:"
ps -eo pid=,ppid=,pgid=,stat=,etime=,cmd= \
  | awk '/run_pg_benchmark_bottleneck_matrix.py/ || /parameter_optimizer.py/ || /hybench -t runap/ || /com.hybench.HyBench -t runap/ {print}' \
  | awk '!/awk/' \
  | tee -a "${LOG_PATH}" >&2 || true

if [[ -e "${BLOCK_FILE}" ]]; then
  log "block file content:"
  sed -n '1,20p' "${BLOCK_FILE}" | tee -a "${LOG_PATH}" >&2 || true
else
  log "block file absent"
fi

if [[ "${DRY_RUN}" == "1" ]]; then
  log "dry run only. To preempt, run:"
  log "CONFIRM_PREEMPT_BONECT_XP_GUARD=I_UNDERSTAND_THIS_CAN_POLLUTE_BONECT $0"
  exit 0
fi

log "stopping bonect_external_xp_guard.py processes"
pkill -TERM -f 'tmp/bonect_external_xp_guard.py' 2>/dev/null || true
sleep 2
pkill -KILL -f 'tmp/bonect_external_xp_guard.py' 2>/dev/null || true

if [[ -e "${BLOCK_FILE}" ]]; then
  log "removing block file"
  rm -f "${BLOCK_FILE}"
fi

log "preempt complete. Bonect benchmark processes may still be running and can still share host CPU/IO/memory."
log "Start binary-plan HyBench XP explicitly with:"
log "BINARY_PLAN_TUNING_HYBENCHXP_IGNORE_EXTERNAL_GUARD=1 ${ROOT_DIR}/shell/run_binary_plan_tuning_hybenchxp.sh all"
