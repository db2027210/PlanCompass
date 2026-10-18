#!/usr/bin/env bash
# Stop continuous host resource guard started by start_host_resource_guard.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$ROOT/runtime_status/host_resource_guard/guard.pid"

if [[ ! -f "$PIDFILE" ]]; then
  echo "no pidfile at $PIDFILE"
  exit 0
fi
pid="$(cat "$PIDFILE" 2>/dev/null || true)"
if [[ -z "${pid:-}" ]]; then
  rm -f "$PIDFILE"
  echo "empty pidfile removed"
  exit 0
fi
if ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$PIDFILE"
  echo "not running (stale pid $pid removed)"
  exit 0
fi
kill -TERM "$pid" 2>/dev/null || true
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PIDFILE"
    echo "stopped pid=$pid"
    exit 0
  fi
  sleep 0.3
done
kill -KILL "$pid" 2>/dev/null || true
rm -f "$PIDFILE"
echo "killed pid=$pid"
