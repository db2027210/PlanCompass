#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 REMOTE_STAGE_DIRECTORY" >&2
  exit 64
fi

stage=$1
python_bin=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
base=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726/tpch_final_algorithm_fresh0_v41_robust4_handoff_20260803
loop=$base/target10_loop_v41
cycle=$loop/cycle_004
dynamic=$cycle/dynamic_expansion_v32
state=$loop/controller_state.json
formal=$base/adopted_v40_formal/formal_run/formal_state.json
counter=$base/formal_search_accepted_counter.txt
lock=$loop/controller.lock
checkpoint=$dynamic/explain_checkpoint.json
failure_log=$dynamic/expand_a.log
builder_a_log=$cycle/probe_builder_a.log
builder_b_log=$cycle/probe_builder_b.log
diagnostic_failure_log=$cycle/diagnostic_probe.log
repair_audit=$dynamic/v43_checkpoint_race_repair_audit.json
session=tpch_v42_checkpoint_restart
resume_log=$loop/v43_checkpoint_resume.log

formal_sha=fc262cead29c831ca04c09a7c4d1b2ebf1481636f82e692cd0dcf3b2d3539bc8
counter_sha=4355a46b19d348dc2f57c046f8ef63d4538ebb936000f3c9ee954a27460dd865
old_checkpoint_sha=0455c669b3b7f886d3c6cb0dda7248f73a0e5e5b815a5115ef4a81f0e7fdf069
old_failure_log_sha=35245af008c67caa64b90e7dba598c290d51f96ad5ae411dfb0f9f275f646003

declare -A expected_sha=(
  [tpch_exact_regime_neighbor_expand_v6_checkpointed.py]=8e16189de0f7677792dcd6c8b5e1ab3fc8015e5242497efa385957b89ae70b9c
  [build_tpch_autonomous_successor_top5_v10_stability_acquisition.py]=da43bfb66ee99f60702685d4b39018ab1ea9d365d3210009c85b6afb6612fc9b
  [run_tpch_round29_diagnostic_exact_plan_regime_batch_v43_stability.py]=6cf1b0bcf87611a0f18c4ffc65d8d6b246d0e29cd81935b7f42009b4df603074
  [tpch_fresh10_target10_loop_v32_v7_checkpointed.py]=f5c735de117c129eed442cbbb7caecc07ba34fe6c68d23eb04a56ef654ffeb61
  [tpch_v42_cycle004_checkpoint_restart.py]=fc7b1637d83e4e9ad14fe43474af90ee9695a318ad9315e59e321788152add3d
  [launch_tpch_v42_cycle004_checkpoint_restart.sh]=497778470fe1af2e33cefc97423c25dfc7ccf9e84d072ac33cfb1d394617e8e4
  [tpch_v43_checkpoint_race_zero_call_repair.py]=46f29c699a0b82764a0ff033a8efef8bcdaa104482a6a6696ad0bde412c4febc
  [tpch_v43_postlaunch_readonly_audit.py]=71ee14e3bcd0b6633cb1398d57201935a78686ba5b89a8817cae05661adee11a
)

file_sha() {
  sha256sum "$1" | awk '{print $1}'
}

require_sha() {
  local path=$1
  local expected=$2
  test -f "$path"
  test "$(file_sha "$path")" = "$expected"
}

require_zero_workload_boundary() {
  test -d "$cycle"
  local forbidden
  forbidden=$(find "$cycle" -type d \( \
    -name 'attempt_*' -o -name 'diagnostic_*' -o -name 'formal_*' \
  \) -print -quit)
  test -z "$forbidden"
}

for name in "${!expected_sha[@]}"; do
  require_sha "$stage/$name" "${expected_sha[$name]}"
done
test -x "$python_bin"
require_sha "$formal" "$formal_sha"
require_sha "$counter" "$counter_sha"
test "$(sed -n '1p' "$counter")" = 1
require_zero_workload_boundary
test -f "$checkpoint"
test -f "$failure_log"
require_sha "$dynamic/expanded_candidates.a.json" c75e3bb2c5ba6a144de0d1f089e68a60128acd7d27b6547a3a001da083f342c0
require_sha "$dynamic/expanded_candidates.b.json" 4a3fb6f8338e5b1f869459da655a8604fe418bf1ad66e48dd0b715b2bde5238c
require_sha "$dynamic/audit.json" 4635ff855c6b88ec75aaf53a45ecddbb7995dea35e2ce442634b2953c6863336
require_sha "$checkpoint" 4d069aa00cb391a8a2d5829f1b0e7db61a6c026e93ce8cec9551d744a1d2e104
require_sha "$cycle/probe_top5.a.json" 21f54e1554965a79324db8f6eff4312be6d01f32edb957c7896e640d2c8f4d9e
require_sha "$cycle/probe_top5.b.json" 21f54e1554965a79324db8f6eff4312be6d01f32edb957c7896e640d2c8f4d9e
require_sha "$builder_a_log" e4f2d758b563abdc4e713156e52938c64b524c0454b4a9f5db2b9ff5d5125b7c
require_sha "$builder_b_log" e8657eabd1ee58e503ebdb979e7b0910e4e1946efc869a41fffc1eeb048d6bad
require_sha "$diagnostic_failure_log" e3e41ca6e9257542c0d42773d7dc4aa3b524c084584edd83c0237253d9b0ddb6
grep -F \
  'ValueError: selection contract mismatch' \
  "$diagnostic_failure_log" >/dev/null
test ! -d "$cycle/diagnostic_probe"

state_sha_before=$(file_sha "$state")
jq -e \
  --arg formal "$formal" \
  --arg formal_sha "$formal_sha" \
  --arg counter_sha "$counter_sha" \
  '.schema_version == "tpch_fresh10_target10_loop_v26"
   and .status == "preparing_cycle"
   and .accepted_count == 1
   and .accepted_target == 10
   and .next_cycle == 4
   and (.cycles | length) == 0
   and .resume_state == $formal
   and .v42_recovery.formal_state_sha256 == $formal_sha
   and .v42_recovery.accepted_counter_sha256 == $counter_sha' \
  "$state" >/dev/null
require_sha "$cycle/config.a.json" 8cdc712a9af757ac0f617b6371395441aebdaf060bc7eb54045b54e8b888958f
require_sha "$cycle/config.b.json" 8cdc712a9af757ac0f617b6371395441aebdaf060bc7eb54045b54e8b888958f

if tmux has-session -t "$session" 2>/dev/null; then
  pane_pid=$(tmux list-panes -t "$session" -F '#{pane_pid}' | sed -n '1p')
  test -n "$pane_pid"
  cmdline=$(tr '\0' ' ' < "/proc/$pane_pid/cmdline")
  case "$cmdline" in
    *tpch_v42_cycle004_checkpoint_restart.py*|*launch_tpch_v42_cycle004_checkpoint_restart.sh*) ;;
    *) echo "unexpected process in $session: $cmdline" >&2; exit 70 ;;
  esac
  require_zero_workload_boundary
  tmux kill-session -t "$session"
  for _ in $(seq 1 20); do
    if flock -n "$lock" -c true; then
      break
    fi
    sleep 1
  done
fi
flock -n "$lock" -c true
require_zero_workload_boundary
require_sha "$state" "$state_sha_before"
require_sha "$formal" "$formal_sha"
require_sha "$counter" "$counter_sha"
require_sha "$dynamic/expanded_candidates.a.json" c75e3bb2c5ba6a144de0d1f089e68a60128acd7d27b6547a3a001da083f342c0
require_sha "$dynamic/expanded_candidates.b.json" 4a3fb6f8338e5b1f869459da655a8604fe418bf1ad66e48dd0b715b2bde5238c
require_sha "$dynamic/audit.json" 4635ff855c6b88ec75aaf53a45ecddbb7995dea35e2ce442634b2953c6863336
require_sha "$checkpoint" 4d069aa00cb391a8a2d5829f1b0e7db61a6c026e93ce8cec9551d744a1d2e104
require_sha "$cycle/probe_top5.a.json" 21f54e1554965a79324db8f6eff4312be6d01f32edb957c7896e640d2c8f4d9e
require_sha "$cycle/probe_top5.b.json" 21f54e1554965a79324db8f6eff4312be6d01f32edb957c7896e640d2c8f4d9e
require_sha "$builder_a_log" e4f2d758b563abdc4e713156e52938c64b524c0454b4a9f5db2b9ff5d5125b7c
require_sha "$builder_b_log" e8657eabd1ee58e503ebdb979e7b0910e4e1946efc869a41fffc1eeb048d6bad
require_sha "$diagnostic_failure_log" e3e41ca6e9257542c0d42773d7dc4aa3b524c084584edd83c0237253d9b0ddb6

timestamp=$(date +%Y%m%dT%H%M%S)
backup=/tmp/tpch_v43_predeploy_backup.$timestamp
mkdir -m 700 "$backup"
for name in "${!expected_sha[@]}"; do
  if [[ -f "/tmp/$name" ]]; then
    cp -p "/tmp/$name" "$backup/$name"
  fi
  cp -p "$stage/$name" "/tmp/$name"
  require_sha "/tmp/$name" "${expected_sha[$name]}"
done

if [[ ! -f "$repair_audit" ]]; then
  require_sha "$checkpoint" "$old_checkpoint_sha"
  require_sha "$failure_log" "$old_failure_log_sha"
  "$python_bin" -u /tmp/tpch_v43_checkpoint_race_zero_call_repair.py \
    --preflight-only \
    --expected-controller-state-sha256 "$state_sha_before" \
    --expected-formal-state-sha256 "$formal_sha" \
    --expected-counter-sha256 "$counter_sha" \
    --expected-checkpoint-sha256 "$old_checkpoint_sha" \
    --expected-failure-log-sha256 "$old_failure_log_sha" \
    > /tmp/tpch_v43_migration_repair_preflight.json
  jq -e \
    '.status == "audit_passed_ready_to_repair"
     and .accepted_count_before == 1
     and .accepted_count_after == 1
     and .physical_explain_invocations_observed == 22
     and .cached_explain_result_count == 22
     and .lost_result_physical_explain_invocations == 5
     and .new_explain_invocations == 0
     and .new_workload_calls == 0
     and .formal_state_mutated == false
     and .accepted_counter_mutated == false
     and .controller_state_mutated == false' \
    /tmp/tpch_v43_migration_repair_preflight.json >/dev/null
  "$python_bin" -u /tmp/tpch_v43_checkpoint_race_zero_call_repair.py \
    --apply-repair-only \
    --expected-controller-state-sha256 "$state_sha_before" \
    --expected-formal-state-sha256 "$formal_sha" \
    --expected-counter-sha256 "$counter_sha" \
    --expected-checkpoint-sha256 "$old_checkpoint_sha" \
    --expected-failure-log-sha256 "$old_failure_log_sha" \
    > /tmp/tpch_v43_migration_repair_apply.json
  jq -e \
    '.status == "checkpoint_repair_applied_zero_call"
     and .accepted_count_after == 1
     and .new_explain_invocations == 0
     and .new_workload_calls == 0
     and .formal_state_mutated == false
     and .accepted_counter_mutated == false
     and .controller_state_mutated == false' \
    /tmp/tpch_v43_migration_repair_apply.json >/dev/null
else
  jq -e \
    '.contract == "tpch_v43_checkpoint_race_zero_call_repair_v1"
     and .status == "checkpoint_repair_applied_zero_call"
     and .accepted_count_after == 1
     and .new_explain_invocations == 0
     and .new_workload_calls == 0' \
    "$repair_audit" >/dev/null
fi

jq -e \
  --arg formal_sha "$formal_sha" \
  '.schema_version == "tpch_explain_expansion_checkpoint_v1"
   and .input_binding.resume_state_sha256 == $formal_sha
   and .input_binding.maximum_candidate_estimations == 128
   and .input_binding.maximum_explain_invocations == 3000
   and .live_explain_invocations >= 22
   and .lost_checkpoint_results >= 0
   and .live_explain_invocations == ((.psql_results | length) + .lost_checkpoint_results)
   and .lost_result_physical_explain_invocations == 5
   and .lost_result_outputs_present_in_cache == true' \
  "$checkpoint" >/dev/null
require_sha "$state" "$state_sha_before"
require_sha "$formal" "$formal_sha"
require_sha "$counter" "$counter_sha"
require_zero_workload_boundary

"$python_bin" -u /tmp/tpch_v43_postlaunch_readonly_audit.py \
  --cycle-root "$cycle" \
  --controller-state "$state" \
  --formal-state "$formal" \
  --accepted-counter "$counter" \
  --expected-controller-state-sha256 "$state_sha_before" \
  --expected-formal-state-sha256 "$formal_sha" \
  --expected-counter-sha256 "$counter_sha" \
  > /tmp/tpch_v43_migration_prelaunch_audit.json
jq -e \
  '.accepted_count == 1
   and .read_only_audit == true
   and .expansion.legacy_complete_semantic_reseal == true
   and .expansion.new_explain_invocations_for_reseal == 0
   and .status == "semantic_top5_ready_before_diagnostic"
   and .top5.selection_sha256 == "21f54e1554965a79324db8f6eff4312be6d01f32edb957c7896e640d2c8f4d9e"
   and .top5.roles == ["trusted", "trusted", "trusted", "probe", "probe"]
   and .top5.stability_acquisition_mode == true
   and .top5.formal_admission_requires_repeat == true
  ' \
  /tmp/tpch_v43_migration_prelaunch_audit.json >/dev/null

diagnostic_quarantine=$loop/quarantine_cycle004_pre_v43_runner_zero_call.$timestamp
test ! -e "$diagnostic_quarantine"
mkdir -m 700 "$diagnostic_quarantine"
mv "$diagnostic_failure_log" "$diagnostic_quarantine/diagnostic_probe.log"
require_sha \
  "$diagnostic_quarantine/diagnostic_probe.log" \
  e3e41ca6e9257542c0d42773d7dc4aa3b524c084584edd83c0237253d9b0ddb6
test ! -e "$diagnostic_failure_log"
jq -n \
  --arg source_sha e3e41ca6e9257542c0d42773d7dc4aa3b524c084584edd83c0237253d9b0ddb6 \
  --arg top5_sha 21f54e1554965a79324db8f6eff4312be6d01f32edb957c7896e640d2c8f4d9e \
  '{
    contract: "tpch_v43_zero_call_diagnostic_runner_failure_quarantine_v1",
    status: "quarantined_before_compatible_runner_resume",
    diagnostic_failure_log_sha256: $source_sha,
    reused_top5_sha256: $top5_sha,
    physical_workload_calls: 0,
    valid_replays: 0,
    accepted_mutated: false,
    incumbent_mutated: false,
    range_mutated: false,
    gradient_mutated: false,
    source_deleted: false,
    source_preserved_by_atomic_rename: true
  }' > "$diagnostic_quarantine/audit.json"

if tmux has-session -t "$session" 2>/dev/null; then
  echo "refuse duplicate tmux session: $session" >&2
  exit 71
fi
tmux new-session -d -s "$session" \
  "exec bash /tmp/launch_tpch_v42_cycle004_checkpoint_restart.sh resume '$state_sha_before' '$formal_sha' '$counter_sha' >'$resume_log' 2>&1"
sleep 2
tmux has-session -t "$session"
pane_pid=$(tmux list-panes -t "$session" -F '#{pane_pid}' | sed -n '1p')
test -r "/proc/$pane_pid/cmdline"
require_sha "$formal" "$formal_sha"
require_sha "$counter" "$counter_sha"
test "$(sed -n '1p' "$counter")" = 1
state_sha_after_launch=$(file_sha "$state")

"$python_bin" -u /tmp/tpch_v43_postlaunch_readonly_audit.py \
  --cycle-root "$cycle" \
  --controller-state "$state" \
  --formal-state "$formal" \
  --accepted-counter "$counter" \
  --expected-controller-state-sha256 "$state_sha_after_launch" \
  --expected-formal-state-sha256 "$formal_sha" \
  --expected-counter-sha256 "$counter_sha" \
  > /tmp/tpch_v43_migration_postlaunch_audit.json
jq -e \
  '.accepted_count == 1
   and .read_only_audit == true
   and (.status == "checkpoint_expansion_running"
        or .status == "checkpoint_expansion_partial"
        or .status == "expansion_complete_waiting_top5"
        or .status == "semantic_top5_ready_before_diagnostic"
        or (.status | startswith("diagnostic_")))' \
  /tmp/tpch_v43_migration_postlaunch_audit.json >/dev/null

jq -n \
  --arg state_sha_before "$state_sha_before" \
  --arg state_sha_after_launch "$state_sha_after_launch" \
  --arg formal_sha "$formal_sha" \
  --arg counter_sha "$counter_sha" \
  --arg checkpoint_sha "$(file_sha "$checkpoint")" \
  --arg backup "$backup" \
  --arg diagnostic_quarantine "$diagnostic_quarantine" \
  --arg session "$session" \
  --arg pane_pid "$pane_pid" \
  '{
    contract: "tpch_v43_safe_migration_and_resume_v1",
    status: "launched_after_fail_closed_migration",
    accepted_count: 1,
    state_sha_before: $state_sha_before,
    state_sha_after_launch: $state_sha_after_launch,
    formal_state_sha256: $formal_sha,
    accepted_counter_sha256: $counter_sha,
    checkpoint_sha256: $checkpoint_sha,
    artifact_backup: $backup,
    zero_call_failure_quarantine: $diagnostic_quarantine,
    tmux_session: $session,
    pane_pid: ($pane_pid | tonumber),
    migration_workload_calls: 0,
    checkpoint_reused: true
  }' > /tmp/tpch_v43_migration_result.json
cat /tmp/tpch_v43_migration_result.json
