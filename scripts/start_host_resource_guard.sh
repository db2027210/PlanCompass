#!/usr/bin/env bash
# Start continuous host resource guard (LIVE kill on breach).
# Usage:
#   scripts/start_host_resource_guard.sh           # live, interval 15s
#   scripts/start_host_resource_guard.sh --dry-run # log only
#   INTERVAL=30 scripts/start_host_resource_guard.sh

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/runtime_status/host_resource_guard"
mkdir -p "$STATE"
PIDFILE="$STATE/guard.pid"
LOG="$STATE/guard_$(date +%Y%m%d).log"
INTERVAL="${INTERVAL:-15}"
MODE_ARGS=(--live)
EXTRA_ARGS=()
while (($#)); do
  case "$1" in
    --dry-run) MODE_ARGS=() ;;  # omit --live
    --live) MODE_ARGS=(--live) ;;
    --exclude-cmdline|--exclude-docker-name)
      if (($# < 2)); then
        echo "missing value for $1" >&2
        exit 2
      fi
      EXTRA_ARGS+=("$1" "$2")
      shift
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
  shift
done

if [[ -f "$PIDFILE" ]]; then
  old="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "${old:-}" ]] && kill -0 "$old" 2>/dev/null; then
    echo "already running pid=$old log=$LOG"
    ps -p "$old" -o pid,etime,cmd=
    exit 0
  fi
fi

cd "$ROOT"
# Single log stream: nohup redirects stdout/stderr; do not also pass --log-file
# (that would duplicate every line).
nohup python3 -u scripts/host_resource_guard.py \
  "${MODE_ARGS[@]}" \
  --interval "$INTERVAL" \
  "${EXTRA_ARGS[@]}" \
  >>"$LOG" 2>&1 &
echo $! >"$PIDFILE"
sleep 1
pid="$(cat "$PIDFILE")"
if kill -0 "$pid" 2>/dev/null; then
  echo "STARTED pid=$pid interval=${INTERVAL}s mode=${MODE_ARGS[*]:-dry-run} log=$LOG"
  tail -n 5 "$LOG" || true
else
  echo "FAILED to start; last log:" >&2
  tail -n 40 "$LOG" >&2 || true
  exit 1
fi
