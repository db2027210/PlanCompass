#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: $0 preflight|apply|resume CONTROLLER_SHA FORMAL_SHA COUNTER_SHA" >&2
  exit 64
fi

mode=$1
controller_sha=$2
formal_sha=$3
counter_sha=$4
controller=/tmp/tpch_v42_cycle004_checkpoint_restart.py
python_bin=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11

case "$mode" in
  preflight) mode_flag=--preflight-only ;;
  apply) mode_flag=--apply-recovery-only ;;
  resume) mode_flag=--resume ;;
  *) echo "invalid mode: $mode" >&2; exit 64 ;;
esac

exec "$python_bin" -u "$controller" \
  "$mode_flag" \
  --expected-controller-state-sha256 "$controller_sha" \
  --expected-formal-state-sha256 "$formal_sha" \
  --expected-counter-sha256 "$counter_sha"
