#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 STAGE_DIRECTORY" >&2
  exit 64
fi

stage=$1
campaign=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726/tpch_final_algorithm_fresh0_v41_robust4_handoff_20260803
loop=$campaign/target10_loop_v41
state=$loop/controller_state.json
counter=$campaign/formal_search_accepted_counter.txt
expected_controller_state_sha=d1431ae426c85f2447012b2b1bdfab83b56a4f1382a07feb5b9cba1b8303c8e2
expected_formal_state_sha=fc262cead29c831ca04c09a7c4d1b2ebf1481636f82e692cd0dcf3b2d3539bc8
expected_counter_sha=4355a46b19d348dc2f57c046f8ef63d4538ebb936000f3c9ee954a27460dd865

verify_sha() {
  local path=$1
  local expected=$2
  [[ -f "$path" ]]
  [[ "$(sha256sum "$path" | awk '{print $1}')" == "$expected" ]]
}

verify_sha "$stage/deploy_tpch_v52_safe_unknown_runner_compatibility.sh" be85975f938fc3eb2c65ea4ae9ae5e1abc92558ddb57c8be76f8fdf4ba8dfeee
verify_sha "$state" "$expected_controller_state_sha"
verify_sha "$counter" "$expected_counter_sha"
test "$(jq -r '.status' "$state")" = "preparing_cycle"
test "$(jq -r '.accepted_count' "$state")" = "1"
test "$(jq -r '.accepted_target' "$state")" = "10"

cycle=$(jq -r '.next_cycle' "$state")
test "$cycle" = "6"
formal=$(jq -r '.resume_state' "$state")
verify_sha "$formal" "$expected_formal_state_sha"

cycle_root=$loop/cycle_$(printf '%03d' "$cycle")
first=$cycle_root/probe_top5.a.json
second=$cycle_root/probe_top5.b.json
failure_log=$cycle_root/diagnostic_probe.log
probe_sha=$(sha256sum "$first" | awk '{print $1}')
verify_sha "$second" "$probe_sha"
cmp -s "$first" "$second"
failure_log_sha=$(sha256sum "$failure_log" | awk '{print $1}')

bash "$stage/deploy_tpch_v52_safe_unknown_runner_compatibility.sh" \
  "$stage" \
  "$expected_controller_state_sha" \
  "$expected_formal_state_sha" \
  "$expected_counter_sha" \
  "$cycle" \
  "$probe_sha" \
  "$failure_log_sha"

preflight=$(
  /mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11 \
    -u /tmp/recover_tpch_v52_cycle6_preworkload.py preflight \
    --expected-controller-state-sha256 "$expected_controller_state_sha" \
    --expected-formal-state-sha256 "$expected_formal_state_sha" \
    --expected-counter-sha256 "$expected_counter_sha" \
    --expected-cycle "$cycle" \
    --expected-probe-sha256 "$probe_sha" \
    --expected-failure-log-sha256 "$failure_log_sha"
)
jq -e '
  .status == "ready_for_zero_call_recovery" and
  .probe_pair_reused == true and
  .explain_rebuilt == false and
  .workload_calls_added == 0
' <<<"$preflight" >/dev/null

recovery=$(
  /mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11 \
    -u /tmp/recover_tpch_v52_cycle6_preworkload.py apply \
    --expected-controller-state-sha256 "$expected_controller_state_sha" \
    --expected-formal-state-sha256 "$expected_formal_state_sha" \
    --expected-counter-sha256 "$expected_counter_sha" \
    --expected-cycle "$cycle" \
    --expected-probe-sha256 "$probe_sha" \
    --expected-failure-log-sha256 "$failure_log_sha"
)
jq -e '
  .status == "zero_call_recovery_applied" and
  .selection_rebuilt == false and
  .selection_bytes_changed == false and
  .workload_calls_added == 0 and
  .formal_state_mutated == false and
  .accepted_counter_mutated == false
' <<<"$recovery" >/dev/null

controller_preflight=$(
  /tmp/launch_tpch_v52_safe_unknown_runner_compatibility.sh \
    preflight \
    "$expected_controller_state_sha" \
    "$expected_formal_state_sha" \
    "$expected_counter_sha" \
    "$cycle" \
    "$probe_sha"
)
jq -e '
  .v52_contract == "tpch_v52_safe_unknown_runner_compatibility_v1" and
  .safe_unknown_selection_reused == true and
  .explain_rebuilt == false and
  .new_workload_calls == 0 and
  .formal_state_mutated == false and
  .accepted_counter_mutated == false and
  .runner_compatibility_translation_only == true
' <<<"$controller_preflight" >/dev/null

verify_sha "$state" "$expected_controller_state_sha"
verify_sha "$formal" "$expected_formal_state_sha"
verify_sha "$counter" "$expected_counter_sha"
verify_sha "$first" "$probe_sha"
verify_sha "$second" "$probe_sha"

jq -n \
  --arg controller_state_sha "$expected_controller_state_sha" \
  --arg formal_state_sha "$expected_formal_state_sha" \
  --arg counter_sha "$expected_counter_sha" \
  --arg probe_sha "$probe_sha" \
  --arg failure_log_sha "$failure_log_sha" \
  --argjson cycle "$cycle" \
  '{
    status: "v52_deployed_recovered_and_preflighted_zero_call",
    controller_state_sha256: $controller_state_sha,
    formal_state_sha256: $formal_state_sha,
    accepted_counter_sha256: $counter_sha,
    probe_selection_sha256: $probe_sha,
    quarantined_failure_log_sha256: $failure_log_sha,
    cycle: $cycle,
    accepted_count: 1,
    workload_started: false,
    selection_rebuilt: false,
    explain_rebuilt: false,
    next: "separate_explicit_v52_resume"
  }'
