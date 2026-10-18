#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: $0 preflight|resume CONTROLLER_SHA FORMAL_SHA COUNTER_SHA CYCLE" >&2
  exit 64
fi

mode=$1
controller_state_sha=$2
formal_state_sha=$3
counter_sha=$4
cycle=$5
controller=/tmp/tpch_v50_trusted_evidence_acquisition.py
builder=/tmp/build_tpch_autonomous_successor_top5_v11_evidence_acquisition.py
python_bin=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11

verify_sha() {
  local path=$1
  local expected=$2
  [[ -f "$path" ]]
  [[ "$(sha256sum "$path" | awk '{print $1}')" == "$expected" ]]
}

verify_sha "$controller" 7deb02401183c6934d3c09fcfad76c7f7e73ab5d226e5cf9d0f772c3e19ef6d6
verify_sha "$builder" ff495fda6a4c887b2dd9ebf7761c131e76da1d14cdaef87831b508215437e27f
verify_sha /tmp/validate_tpch_v50_liveness_fix_boundary.py cf957dca63f22edc7b69431c7a749b5e55286852fee2e5d50e80ddec7f1e3d31
verify_sha /tmp/tpch_v49_preparing_cycle_continuation.py 40231d64d94c98af5d98e28b9e27c82933aa46f9a36d4a4113d2cb7da747c405
verify_sha /tmp/tpch_v48_prethird_feasibility_binding.py 5bf7ff767338860f3e86576c9ef7e5db5b4ce60d111397e89f15776311397458
verify_sha /tmp/tpch_v41_preformal_handoff.py bb81fde56677fc2c19b1399e708005e3b8e950169643c5fe777da94e47b36a41
verify_sha /tmp/tpch_fresh10_target10_loop_v32_v8_gate_binding.py a7174eb59bd467f51a1e55cff4b98f89ed27999cc4ca329ed291cdea1b1f9a69
verify_sha /tmp/tpch_exact_regime_neighbor_expand_v6_checkpointed.py 8e16189de0f7677792dcd6c8b5e1ab3fc8015e5242497efa385957b89ae70b9c
verify_sha /tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v45_none_return.py af90bc7f2e145de14d83449513016cd143a756f6cd7da1a5c015706ea60c55a2

case "$mode" in
  preflight) mode_flag=--preflight-only ;;
  resume) mode_flag=--resume ;;
  *) echo "invalid mode: $mode" >&2; exit 64 ;;
esac

exec "$python_bin" -u "$controller" \
  "$mode_flag" \
  --expected-controller-state-sha256 "$controller_state_sha" \
  --expected-formal-state-sha256 "$formal_state_sha" \
  --expected-counter-sha256 "$counter_sha" \
  --expected-cycle "$cycle"
