#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: $0 preflight-clean5|apply-clean5|resume-clean5|preflight-liveness|resume-liveness CONTROLLER_SHA FORMAL_SHA COUNTER_SHA CYCLE" >&2
  exit 64
fi

mode=$1
controller_state_sha=$2
formal_state_sha=$3
counter_sha=$4
cycle=$5
controller=/tmp/tpch_v45_clean5_resume_and_liveness.py
runner=/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v45_none_return.py
python_bin=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11

verify_sha() {
  local path=$1
  local expected=$2
  [[ -f "$path" ]]
  [[ "$(sha256sum "$path" | awk '{print $1}')" == "$expected" ]]
}

verify_sha "$controller" e31089dd10e6f5e7136ca4504b8c76c96277809723f700c814c0051f74f1317a
verify_sha "$runner" af90bc7f2e145de14d83449513016cd143a756f6cd7da1a5c015706ea60c55a2
verify_sha /tmp/tpch_v41_preformal_handoff.py bb81fde56677fc2c19b1399e708005e3b8e950169643c5fe777da94e47b36a41
verify_sha /tmp/tpch_fresh10_target10_loop_v32_v7_checkpointed.py f5c735de117c129eed442cbbb7caecc07ba34fe6c68d23eb04a56ef654ffeb61
verify_sha /tmp/tpch_exact_regime_neighbor_expand_v6_checkpointed.py 8e16189de0f7677792dcd6c8b5e1ab3fc8015e5242497efa385957b89ae70b9c
verify_sha /tmp/build_tpch_autonomous_successor_top5_v10_stability_acquisition.py da43bfb66ee99f60702685d4b39018ab1ea9d365d3210009c85b6afb6612fc9b

case "$mode" in
  preflight-clean5) mode_flag=--preflight-clean5-only ;;
  apply-clean5) mode_flag=--apply-clean5-only ;;
  resume-clean5) mode_flag=--resume-clean5 ;;
  preflight-liveness) mode_flag=--preflight-liveness-only ;;
  resume-liveness) mode_flag=--resume-liveness ;;
  *) echo "invalid mode: $mode" >&2; exit 64 ;;
esac

exec "$python_bin" -u "$controller" \
  "$mode_flag" \
  --expected-controller-state-sha256 "$controller_state_sha" \
  --expected-formal-state-sha256 "$formal_state_sha" \
  --expected-counter-sha256 "$counter_sha" \
  --expected-cycle "$cycle"
