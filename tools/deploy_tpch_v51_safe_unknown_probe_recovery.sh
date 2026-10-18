#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: $0 STAGE_DIRECTORY CONTROLLER_STATE_SHA FORMAL_STATE_SHA COUNTER_SHA CYCLE" >&2
  exit 64
fi

stage=$1
controller_state_sha=$2
formal_state_sha=$3
counter_sha=$4
cycle=$5
base_controller_name=tpch_v50_trusted_evidence_acquisition.py
controller_name=tpch_v51_safe_unknown_probe_recovery.py
builder_name=build_tpch_autonomous_successor_top5_v11_evidence_acquisition.py
launcher_name=launch_tpch_v51_safe_unknown_probe_recovery.sh
validator_name=validate_tpch_v50_liveness_fix_boundary.py
python_bin=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
controller_lock=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726/tpch_final_algorithm_fresh0_v41_robust4_handoff_20260803/target10_loop_v41/controller.lock

verify_sha() {
  local path=$1
  local expected=$2
  [[ -f "$path" ]]
  [[ "$(sha256sum "$path" | awk '{print $1}')" == "$expected" ]]
}

verify_sha "$stage/$base_controller_name" ffb8caadb0ee9738e31c0cff081e623ec3ccea7a09c824613a138183540f59fd
verify_sha "$stage/$controller_name" 2a9aaac13bf3af4d9007466c4fec3e606d9aa41fb5a4ca08da60e28dd80a0d91
verify_sha "$stage/$builder_name" 8447df5251c53b823bca21e89cc345ea903e333f9688468627c20bfd811d7b49
verify_sha "$stage/$launcher_name" cc937e17aa2cd9935730d4efb555affe128eebe65741d8b10fa8a7384f9ebc07
verify_sha "$stage/$validator_name" cf957dca63f22edc7b69431c7a749b5e55286852fee2e5d50e80ddec7f1e3d31

verify_sha /tmp/tpch_v49_preparing_cycle_continuation.py 40231d64d94c98af5d98e28b9e27c82933aa46f9a36d4a4113d2cb7da747c405
verify_sha /tmp/tpch_v48_prethird_feasibility_binding.py 5bf7ff767338860f3e86576c9ef7e5db5b4ce60d111397e89f15776311397458
verify_sha /tmp/tpch_v41_preformal_handoff.py bb81fde56677fc2c19b1399e708005e3b8e950169643c5fe777da94e47b36a41
verify_sha /tmp/tpch_fresh10_target10_loop_v32_v8_gate_binding.py a7174eb59bd467f51a1e55cff4b98f89ed27999cc4ca329ed291cdea1b1f9a69
verify_sha /tmp/tpch_exact_regime_neighbor_expand_v6_checkpointed.py 8e16189de0f7677792dcd6c8b5e1ab3fc8015e5242497efa385957b89ae70b9c
verify_sha /tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v45_none_return.py af90bc7f2e145de14d83449513016cd143a756f6cd7da1a5c015706ea60c55a2

[[ "$controller_state_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$formal_state_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$counter_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$cycle" =~ ^[1-9][0-9]*$ ]]

exec 9>"$controller_lock"
flock -n 9

boundary=$(
  "$python_bin" -u "$stage/$validator_name" \
    --expected-controller-state-sha256 "$controller_state_sha" \
    --expected-formal-state-sha256 "$formal_state_sha" \
    --expected-counter-sha256 "$counter_sha" \
    --expected-cycle "$cycle"
)
jq -e '
  .contract == "tpch_v50_liveness_fix_clean_preparing_cycle_upgrade_v1" and
  .status == "ready_to_continue_clean_preparing_cycle" and
  .safe_to_replace_inactive_v50_code == true and
  .new_workload_calls == 0 and
  .formal_state_mutated == false and
  .accepted_counter_mutated == false
' <<<"$boundary" >/dev/null

if ps -eo stat=,args= | awk '
  $1 !~ /^Z/ &&
  $0 ~ /tpch_(v4[1-9]|v50|fresh10_target10_loop|round29_diagnostic)/ &&
  $0 !~ /(deploy_tpch_v5[01]|apply_tpch_v5[01]|validate_tpch_v50_liveness_fix_boundary)/ { found=1 }
  END { exit(found ? 0 : 1) }
'; then
  echo "active TPC-H controller/diagnostic detected; refusing deployment" >&2
  exit 73
fi

for name in "$base_controller_name" "$controller_name" "$builder_name" "$launcher_name" "$validator_name"; do
  source_path=$stage/$name
  destination=/tmp/$name
  source_sha=$(sha256sum "$source_path" | awk '{print $1}')
  if [[ -e "$destination" ]]; then
    destination_sha=$(sha256sum "$destination" | awk '{print $1}')
    if [[ "$destination_sha" == "$source_sha" ]]; then
      continue
    fi
    case "$name:$destination_sha" in
      "$base_controller_name:8662a1defd9ad44eb266c70ac6ff33dd1fe1a4c4d74034e4b675114c6aea220a"|\
      "$base_controller_name:dddb9edd6cc6d27a01352bc0d9255f3f87c3f3555ec8ef69f0f2b4e9c93a6036"|\
      "$base_controller_name:7deb02401183c6934d3c09fcfad76c7f7e73ab5d226e5cf9d0f772c3e19ef6d6"|\
      "$base_controller_name:8565d8e926fdad9fc9eb9b371bb0759c29fb02bc4a71b71e0e2931d5a6c70718"|\
      "$builder_name:f020d8fe0cecbb62b72fde28bee88ad8ad48529a8d568c4868cc523c4304ff73"|\
      "$builder_name:ff495fda6a4c887b2dd9ebf7761c131e76da1d14cdaef87831b508215437e27f"|\
      "$builder_name:8ba1a5907ff01a1ac415c4eedff1555ad03df43186fad4471f88fd6039b2d893"|\
      "$validator_name:0e6983eb0aca8fcfc62254541818954959b55274ed10c710695421f5be8792a4")
        backup=${destination}.pre_liveness_fix.${destination_sha}
        if [[ -e "$backup" ]]; then
          verify_sha "$backup" "$destination_sha"
        else
          cp -p "$destination" "$backup"
          verify_sha "$backup" "$destination_sha"
        fi
        ;;
      *)
        echo "destination exists with unrecognized SHA: $destination" >&2
        exit 74
        ;;
    esac
  fi
  temporary=${destination}.deploy.$$
  cp "$source_path" "$temporary"
  [[ "$(sha256sum "$temporary" | awk '{print $1}')" == "$source_sha" ]]
  chmod "$(stat -c '%a' "$source_path")" "$temporary"
  mv "$temporary" "$destination"
done

verify_sha /tmp/$base_controller_name ffb8caadb0ee9738e31c0cff081e623ec3ccea7a09c824613a138183540f59fd
verify_sha /tmp/$controller_name 2a9aaac13bf3af4d9007466c4fec3e606d9aa41fb5a4ca08da60e28dd80a0d91
verify_sha /tmp/$builder_name 8447df5251c53b823bca21e89cc345ea903e333f9688468627c20bfd811d7b49
verify_sha /tmp/$launcher_name cc937e17aa2cd9935730d4efb555affe128eebe65741d8b10fa8a7384f9ebc07
verify_sha /tmp/$validator_name cf957dca63f22edc7b69431c7a749b5e55286852fee2e5d50e80ddec7f1e3d31

echo '{"status":"v51_deployed_at_validated_preparing_cycle","new_workload_calls":0,"next":"run_new_v51_preflight"}'
