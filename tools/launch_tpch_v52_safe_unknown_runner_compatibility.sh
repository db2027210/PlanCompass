#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 6 ]]; then
  echo "usage: $0 preflight|resume CONTROLLER_SHA FORMAL_SHA COUNTER_SHA CYCLE PROBE_SHA" >&2
  exit 64
fi

mode=$1
controller_state_sha=$2
formal_state_sha=$3
counter_sha=$4
cycle=$5
probe_sha=$6
controller=/tmp/tpch_v52_safe_unknown_runner_compatibility.py
runner=/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v52_safe_unknown.py
python_bin=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
campaign=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726/tpch_final_algorithm_fresh0_v41_robust4_handoff_20260803
loop=$campaign/target10_loop_v41

verify_sha() {
  local path=$1
  local expected=$2
  [[ -f "$path" ]]
  [[ "$(sha256sum "$path" | awk '{print $1}')" == "$expected" ]]
}

verify_sha "$controller" df391ebea94bb98dad30144ff891cb9dd8978f7f1622a0b8291e147603ac464d
verify_sha "$runner" afa8d50a0ec592102a1d191da9e6ebe223fff02284d63c7782b0dffcd4935495
verify_sha /tmp/tpch_v51_safe_unknown_probe_recovery.py 2a9aaac13bf3af4d9007466c4fec3e606d9aa41fb5a4ca08da60e28dd80a0d91
verify_sha /tmp/tpch_v50_trusted_evidence_acquisition.py ffb8caadb0ee9738e31c0cff081e623ec3ccea7a09c824613a138183540f59fd
verify_sha /tmp/build_tpch_autonomous_successor_top5_v11_evidence_acquisition.py 8447df5251c53b823bca21e89cc345ea903e333f9688468627c20bfd811d7b49
verify_sha /tmp/validate_tpch_v50_liveness_fix_boundary.py cf957dca63f22edc7b69431c7a749b5e55286852fee2e5d50e80ddec7f1e3d31
verify_sha /tmp/tpch_v49_preparing_cycle_continuation.py 40231d64d94c98af5d98e28b9e27c82933aa46f9a36d4a4113d2cb7da747c405
verify_sha /tmp/tpch_v48_prethird_feasibility_binding.py 5bf7ff767338860f3e86576c9ef7e5db5b4ce60d111397e89f15776311397458
verify_sha /tmp/tpch_v41_preformal_handoff.py bb81fde56677fc2c19b1399e708005e3b8e950169643c5fe777da94e47b36a41
verify_sha /tmp/tpch_fresh10_target10_loop_v32_v8_gate_binding.py a7174eb59bd467f51a1e55cff4b98f89ed27999cc4ca329ed291cdea1b1f9a69
verify_sha /tmp/tpch_exact_regime_neighbor_expand_v6_checkpointed.py 8e16189de0f7677792dcd6c8b5e1ab3fc8015e5242497efa385957b89ae70b9c
verify_sha /tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v45_none_return.py af90bc7f2e145de14d83449513016cd143a756f6cd7da1a5c015706ea60c55a2

[[ "$controller_state_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$formal_state_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$counter_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$probe_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$cycle" =~ ^[1-9][0-9]*$ ]]

first=$loop/cycle_$(printf '%03d' "$cycle")/probe_top5.a.json
second=$loop/cycle_$(printf '%03d' "$cycle")/probe_top5.b.json
verify_sha "$first" "$probe_sha"
verify_sha "$second" "$probe_sha"
cmp -s "$first" "$second"

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
