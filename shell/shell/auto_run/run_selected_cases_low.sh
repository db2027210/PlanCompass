#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
RUN_SCRIPT="$SCRIPT_DIR/experiment_run.sh"

if [ ! -x "$RUN_SCRIPT" ]; then
  echo "ERROR: run script not executable: $RUN_SCRIPT"
  exit 1
fi

usage() {
  cat <<'EOF'
Usage:
  run_selected_cases_low.sh [options]

Options:
  --containers LIST        Container indexes, comma-separated (default: 1,5)
  --cases LIST             Case IDs, comma-separated (default is built-in selected list)
  --continue-on-fail       Continue on failure
  --dry-run                Print plan only
  -h, --help               Show help

Environment:
  BENCH_TIME_SEC           Workload time in seconds (default: 1200)
  SKIP_RSYNC_DATA          0/1, passed to experiment_run.sh (default: 0)
  RESULT_ROOT              Unified result root for all containers (optional)
  DISK1_RESULT_ROOT        Result root for node1-4 (optional)
  DISK2_RESULT_ROOT        Result root for node5-8 (optional)

Built-in cases (mapped from your request):
  TPCC: c6 c9 c26 c29
  TPCH: h6 h8 h25 h29
  CH:   ch17 ch18 ch57 ch58
EOF
}

CONTAINERS="1,5"
CONTINUE_ON_FAIL=0
DRY_RUN=0
CUSTOM_CASES=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --containers) CONTAINERS="$2"; shift 2 ;;
    --cases) CUSTOM_CASES="$2"; shift 2 ;;
    --continue-on-fail) CONTINUE_ON_FAIL=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

declare -a CASES=()
if [ -n "$CUSTOM_CASES" ]; then
  IFS=',' read -r -a _raw_cases <<< "$CUSTOM_CASES"
  for c in "${_raw_cases[@]}"; do
    c="$(echo "$c" | tr -d '[:space:]')"
    [ -n "$c" ] && CASES+=("$c")
  done
else
  CASES=(c6 c9 c26 c29 h6 h8 h25 h29 ch17 ch18 ch57 ch58)
fi

declare -a CONTAINER_LIST=()
IFS=',' read -r -a _raw_containers <<< "$CONTAINERS"
for c in "${_raw_containers[@]}"; do
  c="$(echo "$c" | tr -d '[:space:]')"
  if ! [[ "$c" =~ ^[1-8]$ ]]; then
    echo "ERROR: invalid container index: $c (must be 1..8)"
    exit 1
  fi
  CONTAINER_LIST+=("$c")
done

if [ "${#CONTAINER_LIST[@]}" -eq 0 ] || [ "${#CASES[@]}" -eq 0 ]; then
  echo "ERROR: empty containers or cases."
  exit 1
fi

BENCH_TIME_SEC="${BENCH_TIME_SEC:-1200}"
SKIP_RSYNC_DATA="${SKIP_RSYNC_DATA:-0}"
DEFAULT_DISK1_RESULT_ROOT="/disk3/anonymous/experiment_result1_disk1"
DEFAULT_DISK2_RESULT_ROOT="/disk3/anonymous/experiment_result1_disk2"

resolve_result_root() {
  local container_idx="$1"
  if [ -n "${RESULT_ROOT:-}" ]; then
    printf '%s\n' "$RESULT_ROOT"
    return 0
  fi
  if [ "$container_idx" -ge 5 ]; then
    printf '%s\n' "${DISK2_RESULT_ROOT:-$DEFAULT_DISK2_RESULT_ROOT}"
  else
    printf '%s\n' "${DISK1_RESULT_ROOT:-$DEFAULT_DISK1_RESULT_ROOT}"
  fi
}

echo "Mode: low"
echo "Containers: ${CONTAINER_LIST[*]}"
echo "Cases: ${CASES[*]}"
echo "BENCH_TIME_SEC: $BENCH_TIME_SEC"
echo "SKIP_RSYNC_DATA: $SKIP_RSYNC_DATA"
echo "CONTINUE_ON_FAIL: $CONTINUE_ON_FAIL"

if [ "$DRY_RUN" -eq 1 ]; then
  exit 0
fi

failed=0
for i in "${!CASES[@]}"; do
  case_id="${CASES[$i]}"
  cidx="${CONTAINER_LIST[$((i % ${#CONTAINER_LIST[@]}))]}"
  case_root="$(resolve_result_root "$cidx")"

  echo "=================================================="
  echo "Run case=$case_id on container=$cidx"
  if [ -n "$case_root" ]; then
    echo "RESULT_ROOT=$case_root"
  fi
  echo "=================================================="

  if [ -n "$case_root" ]; then
    RESULT_ROOT="$case_root" \
    BENCH_TIME_SEC="$BENCH_TIME_SEC" \
    SKIP_RSYNC_DATA="$SKIP_RSYNC_DATA" \
    "$RUN_SCRIPT" low "$case_id" "$cidx" || {
      echo "ERROR: failed case=$case_id container=$cidx"
      failed=1
      if [ "$CONTINUE_ON_FAIL" -ne 1 ]; then
        exit 1
      fi
    }
  else
    BENCH_TIME_SEC="$BENCH_TIME_SEC" \
    SKIP_RSYNC_DATA="$SKIP_RSYNC_DATA" \
    "$RUN_SCRIPT" low "$case_id" "$cidx" || {
      echo "ERROR: failed case=$case_id container=$cidx"
      failed=1
      if [ "$CONTINUE_ON_FAIL" -ne 1 ]; then
        exit 1
      fi
    }
  fi
done

if [ "$failed" -ne 0 ]; then
  exit 1
fi

echo "All selected cases finished."

