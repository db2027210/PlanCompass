#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 7 ]]; then
  echo "usage: $0 STAGE CONTROLLER_SHA FORMAL_SHA COUNTER_SHA CYCLE PROBE_SHA FAILURE_LOG_SHA" >&2
  exit 64
fi

stage=$1
controller_state_sha=$2
formal_state_sha=$3
counter_sha=$4
cycle=$5
probe_sha=$6
failure_log_sha=$7
controller_name=tpch_v52_safe_unknown_runner_compatibility.py
runner_name=run_tpch_round29_diagnostic_exact_plan_regime_batch_v52_safe_unknown.py
recovery_name=recover_tpch_v52_cycle6_preworkload.py
launcher_name=launch_tpch_v52_safe_unknown_runner_compatibility.sh
python_bin=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
controller_lock=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726/tpch_final_algorithm_fresh0_v41_robust4_handoff_20260803/target10_loop_v41/controller.lock

verify_sha() {
  local path=$1
  local expected=$2
  [[ -f "$path" ]]
  [[ "$(sha256sum "$path" | awk '{print $1}')" == "$expected" ]]
}

verify_sha "$stage/$controller_name" df391ebea94bb98dad30144ff891cb9dd8978f7f1622a0b8291e147603ac464d
verify_sha "$stage/$runner_name" afa8d50a0ec592102a1d191da9e6ebe223fff02284d63c7782b0dffcd4935495
verify_sha "$stage/$recovery_name" fa70940c41a506585da939573900b0e25169a01d05b2169d4dc5178414e78a57
verify_sha "$stage/$launcher_name" 31fed29f9145bd5e8f50679ea48711243a670ff8a9add8cc320d2db96801a72d

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
[[ "$failure_log_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$cycle" =~ ^[1-9][0-9]*$ ]]

boundary=$(
  "$python_bin" -u "$stage/$recovery_name" preflight \
    --expected-controller-state-sha256 "$controller_state_sha" \
    --expected-formal-state-sha256 "$formal_state_sha" \
    --expected-counter-sha256 "$counter_sha" \
    --expected-cycle "$cycle" \
    --expected-probe-sha256 "$probe_sha" \
    --expected-failure-log-sha256 "$failure_log_sha"
)
jq -e '
  .contract == "tpch_v52_cycle6_preworkload_runner_compatibility_recovery_v1" and
  .status == "ready_for_zero_call_recovery" and
  .probe_pair_reused == true and
  .explain_rebuilt == false and
  .workload_calls_added == 0 and
  .formal_state_mutated == false and
  .accepted_counter_mutated == false
' <<<"$boundary" >/dev/null

exec 9>"$controller_lock"
flock -n 9

if ps -eo stat=,args= | awk '
  $1 !~ /^Z/ &&
  $0 ~ /tpch_(v4[1-9]|v5[0-2]|fresh10_target10_loop|round29_diagnostic)/ &&
  $0 !~ /(deploy_tpch_v52|apply_tpch_v52|recover_tpch_v52|launch_tpch_v52)/ { found=1 }
  END { exit(found ? 0 : 1) }
'; then
  echo "active TPC-H controller/diagnostic detected; refusing deployment" >&2
  exit 73
fi

for name in "$controller_name" "$runner_name" "$recovery_name" "$launcher_name"; do
  source_path=$stage/$name
  destination=/tmp/$name
  source_sha=$(sha256sum "$source_path" | awk '{print $1}')
  if [[ -e "$destination" ]]; then
    destination_sha=$(sha256sum "$destination" | awk '{print $1}')
    if [[ "$destination_sha" == "$source_sha" ]]; then
      continue
    fi
    echo "destination exists with unrecognized SHA: $destination" >&2
    exit 74
  fi
  temporary=${destination}.deploy.$$
  cp "$source_path" "$temporary"
  verify_sha "$temporary" "$source_sha"
  chmod "$(stat -c '%a' "$source_path")" "$temporary"
  mv "$temporary" "$destination"
done

verify_sha /tmp/$controller_name df391ebea94bb98dad30144ff891cb9dd8978f7f1622a0b8291e147603ac464d
verify_sha /tmp/$runner_name afa8d50a0ec592102a1d191da9e6ebe223fff02284d63c7782b0dffcd4935495
verify_sha /tmp/$recovery_name fa70940c41a506585da939573900b0e25169a01d05b2169d4dc5178414e78a57
verify_sha /tmp/$launcher_name 31fed29f9145bd5e8f50679ea48711243a670ff8a9add8cc320d2db96801a72d

echo '{"status":"v52_deployed_at_validated_cycle6_runner_stop","new_workload_calls":0,"next":"apply_zero_call_recovery"}'
