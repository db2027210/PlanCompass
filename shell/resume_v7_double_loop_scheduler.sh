#!/usr/bin/env bash
# Resume the formal double-loop v7 rootless matrix scheduler after
# infrastructure failures. Does NOT retry baseline sweet-band gate failures
# (a16/a17/a18) — those need workload/gate changes, not blind retries.
set -euo pipefail

ROOT="/home/anonymous/program/resource_adaptive"
SCHED_ROOT="/disk1/resource_adaptive_experiment_matrix_20260718/rootless_scheduled_18_v7_20260720"
CFG="$ROOT/config/double_loop_rootless_vm_scheduler_20260720_v7.json"
PY="/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
LOG="$SCHED_ROOT/scheduler_stdout_resume_full_20260720.log"
PIDFILE="/tmp/dl_v7_scheduler.pid"
LOCK="$SCHED_ROOT/scheduler.lock"

cd "$ROOT"

# Keep KVM usable for this user (session group may not include kvm yet).
if command -v sudo >/dev/null 2>&1; then
  sudo -n setfacl -m u:anonymous:rw /dev/kvm 2>/dev/null || true
fi

# Stop only the v7 scheduler. Leave workers/runners/qemu alone.
while read -r pid; do
  [ -n "$pid" ] || continue
  kill "$pid" 2>/dev/null || true
done < <(pgrep -f "double_loop_vm_task_scheduler.py .*double_loop_rootless_vm_scheduler_20260720_v7.json" || true)
sleep 2
while read -r pid; do
  [ -n "$pid" ] || continue
  kill -9 "$pid" 2>/dev/null || true
done < <(pgrep -f "double_loop_vm_task_scheduler.py .*double_loop_rootless_vm_scheduler_20260720_v7.json" || true)
sleep 1

# Best-effort lock free.
for _ in $(seq 1 10); do
  holders="$(lsof "$LOCK" 2>/dev/null | awk 'NR>1 {print $2}' | sort -u || true)"
  if [ -z "$holders" ]; then
    break
  fi
  for h in $holders; do
    kill -9 "$h" 2>/dev/null || true
  done
  sleep 1
done

# Restart only the scheduler control plane.  Existing workers/runners/QEMU
# processes are intentionally left untouched and are recovered from durable
# state by refresh_state().  Reviewable tasks are retried explicitly after
# their workload/gate diagnosis, never blindly during a control-plane restart.
# The isolated v9 controller owns three experiment pools.  Keep this queue
# to one active VM so the combined campaign never exceeds four.
nohup "$PY" -u shell/double_loop_vm_task_scheduler.py \
  --config "$CFG" \
  --execute --watch --max-active 1 \
  >"$LOG" 2>&1 &
echo $! >"$PIDFILE"
echo "started_pid=$(cat "$PIDFILE")"
echo "log=$LOG"
sleep 4
if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "scheduler_alive"
  ps -p "$(cat "$PIDFILE")" -o pid,etime,cmd
else
  echo "scheduler_dead"
  tail -n 80 "$LOG" || true
  exit 1
fi
