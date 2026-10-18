#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: $0 preflight-liveness|resume-liveness CONTROLLER_SHA FORMAL_SHA COUNTER_SHA CYCLE" >&2
  exit 64
fi

mode=$1
controller_sha=$2
formal_sha=$3
counter_sha=$4
cycle=$5
controller=/tmp/tpch_v44_deterministic_reexploration.py
python_bin=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11

case "$mode" in
  preflight-liveness) mode_flag=--preflight-liveness-only ;;
  resume-liveness) mode_flag=--resume-liveness ;;
  *) echo "invalid mode: $mode" >&2; exit 64 ;;
esac

exec "$python_bin" -u "$controller" \
  "$mode_flag" \
  --expected-controller-state-sha256 "$controller_sha" \
  --expected-formal-state-sha256 "$formal_sha" \
  --expected-counter-sha256 "$counter_sha" \
  --expected-cycle "$cycle"
