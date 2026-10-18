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

test -f "$state"
test -f "$counter"
test "$(jq -r '.status' "$state")" = "preparing_cycle"
test "$(jq -r '.accepted_count' "$state")" = "1"
test "$(jq -r '.accepted_target' "$state")" = "10"

cycle=$(jq -r '.next_cycle' "$state")
formal=$(jq -r '.resume_state' "$state")
test -f "$formal"

controller_state_sha=$(sha256sum "$state" | awk '{print $1}')
formal_state_sha=$(sha256sum "$formal" | awk '{print $1}')
counter_sha=$(sha256sum "$counter" | awk '{print $1}')

bash "$stage/deploy_tpch_v51_safe_unknown_probe_recovery.sh" \
  "$stage" \
  "$controller_state_sha" \
  "$formal_state_sha" \
  "$counter_sha" \
  "$cycle"

/tmp/launch_tpch_v51_safe_unknown_probe_recovery.sh \
  preflight \
  "$controller_state_sha" \
  "$formal_state_sha" \
  "$counter_sha" \
  "$cycle"

jq -n \
  --arg controller_state_sha "$controller_state_sha" \
  --arg formal_state_sha "$formal_state_sha" \
  --arg counter_sha "$counter_sha" \
  --argjson cycle "$cycle" \
  '{
    status: "v51_deployed_and_preflighted_zero_call",
    controller_state_sha256: $controller_state_sha,
    formal_state_sha256: $formal_state_sha,
    accepted_counter_sha256: $counter_sha,
    cycle: $cycle,
    workload_started: false,
    next: "separate_explicit_resume"
  }'
