#!/usr/bin/env bash
set -euo pipefail

# Run on node02 only after the sealed Python files have been copied to /tmp.
# The script first applies a zero-workload-call correction to the current JOB
# incumbent, then obtains one independent exact-point repeat and promotes the
# same Top-5 only if the complete clean5 gate passes.  Formal trusted decisions
# use the median of the two diagnostic scores and the new formal score.

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
P=${B}/job_round8_corrected_top5_v6_20260731
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
STATE=${T}/package/formal_state.json
ACCEPTED_COUNTER=${T}/package/formal_search_accepted_counter.txt
GLOBAL_COUNTER=${B}/replay_counter.txt
FORMAL_REPLAY_COUNTER=${B}/formal_search_replay_counter.txt

FIRST_ACCEPTANCE_SELECTION=${T}/package/top5_promoted_formal.json
FIRST_ACCEPTANCE_EVENTS=${B}/formal10_final_algorithm_fresh10_v33_20260731T1910/replay/events.jsonl
FORMAL_ACCEPTANCE_EVENTS=${T}/batch001/events.jsonl

FIRST_DIAGNOSTIC_SELECTION=${T}/diagnostic_validation_batch003/top5.json
FIRST_DIAGNOSTIC_SUMMARY=${T}/diagnostic_validation_batch003/replay/summary.json

ROOT=${T}/robust_three_observation_v8_20260801
PACKAGE=${ROOT}/package
SECOND=${ROOT}/diagnostic_repeat
FORMAL=${ROOT}/formal
AUDIT_ROOT=${T}/state_recovery_audits

RECOVER=/tmp/recover_job_robust_median_incumbent_v1.py
ACCEPTANCE_AUDITOR=/tmp/audit_job_three_observation_acceptance.py
ROBUST_RUNNER_SOURCE=/tmp/run_job_round8_strict_serial_robust_v7.py
ROBUST_SELECTION_BUILDER_SOURCE=/tmp/build_job_three_observation_formal_selection_v1.py
EXACT_REPEAT_BUILDER_SOURCE=/tmp/build_job_exact_plan_repeat_validation.py
GATE_SOURCE=/tmp/evaluate_job_clean5_gate.py
CONTROLLER_SOURCE=/tmp/job_formal_target10_batch_controller.py

EXPECTED_STATE_SHA=4d18a6e5b4049f5890d0ac3602f3a3bfdba38a39db5d22c819734130575c601b
EXPECTED_ACCEPTANCE_SELECTION_SHA=b80aefae16815373a5648007eda9ea35b648da5f75e0abf2050ce032f5fe1f6d
EXPECTED_FIRST_ACCEPTANCE_EVENTS_SHA=7e24fc8b8275f3db46bc036685acad25c5cc30751ecaa5c410b553c3eb318583
EXPECTED_FORMAL_ACCEPTANCE_EVENTS_SHA=2d7892aba56619b6bc86c09683f65e760b9ec6dd3ee2737a4809a89fa4300fe5
EXPECTED_FIRST_DIAGNOSTIC_SELECTION_SHA=bd68a5158b9432486acc9e1a26042caa10ca916586d7b75f3b65b1f5aeefbd32
EXPECTED_FIRST_DIAGNOSTIC_SUMMARY_SHA=c96de635a9abb552efb40290d0edabf3a069afb1d98fae52da4d3be106b9761f

EXPECTED_RECOVER_SHA=f42bd8a1dd29f9f414644bfd29ca6f01421038af5109edaaf89a53be3713b5cd
EXPECTED_ACCEPTANCE_AUDITOR_SHA=2467cef97cc347376d9724e7ade1bb10e910aa330aeb290fb86aa0b9a2e26e17
EXPECTED_ROBUST_RUNNER_SHA=9251d7fa68276113656826bd6df1777c7b7dfe71b42d012674fbd19ca4b2f09c
EXPECTED_ROBUST_SELECTION_BUILDER_SHA=40c528bb43ecd6b31987daa6d0dec2704fde93da11d510261d854d9eceb532a7
EXPECTED_EXACT_REPEAT_BUILDER_SHA=89c3a2238830087c129cadee76cd5a28248d87ce10b0fd98c6d5f9cb62bdd6cf
EXPECTED_GATE_SHA=72700f5169ca7ba3bd1b527397ac179036ab8bebebb5fb7e977f5be4de2b558e
EXPECTED_CONTROLLER_SHA=df512fdecaa2d98f23144991a75ed4eb68c32ec5cc143ff9d9bfb1232f179356
EXPECTED_BASE_RUNNER_SHA=9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
EXPECTED_CONTRACT_SHA=a5ae7fddcfcd16e1715e35cc5a67c3d3d135b2542ff0e132a4cca5817afddf05

sha() {
  sha256sum "$1" | awk '{print $1}'
}

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha "${path}")" = "${expected}"
}

validate_clean5() {
  local summary=$1
  jq -e '
    .diagnostic_only == true and
    .physical_invocation_count == 5 and
    .successful_real_replay_count == 5 and
    .technical_failure_count == 0 and
    .baseline_params_restored == true and
    .baseline_restore.success == true and
    .initial_state == .final_state and
    .formal_update_flags == {
      "accepted": false,
      "gradient": false,
      "incumbent": false,
      "range": false
    }
  ' "${summary}" >/dev/null
}

# Never overlap a real JOB replay or formal controller.
if pgrep -af "${B}.*(run_job_round8_strict_serial|job_formal_target10_batch_controller|formal_pg_point_runner)" >/dev/null; then
  echo "active JOB replay/controller detected; refusing overlap" >&2
  exit 73
fi

test ! -e "${ROOT}"
require_sha "${STATE}" "${EXPECTED_STATE_SHA}"
require_sha "${FIRST_ACCEPTANCE_SELECTION}" "${EXPECTED_ACCEPTANCE_SELECTION_SHA}"
require_sha "${FIRST_ACCEPTANCE_EVENTS}" "${EXPECTED_FIRST_ACCEPTANCE_EVENTS_SHA}"
require_sha "${FORMAL_ACCEPTANCE_EVENTS}" "${EXPECTED_FORMAL_ACCEPTANCE_EVENTS_SHA}"
require_sha "${FIRST_DIAGNOSTIC_SELECTION}" "${EXPECTED_FIRST_DIAGNOSTIC_SELECTION_SHA}"
require_sha "${FIRST_DIAGNOSTIC_SUMMARY}" "${EXPECTED_FIRST_DIAGNOSTIC_SUMMARY_SHA}"
validate_clean5 "${FIRST_DIAGNOSTIC_SUMMARY}"

require_sha "${RECOVER}" "${EXPECTED_RECOVER_SHA}"
require_sha "${ACCEPTANCE_AUDITOR}" "${EXPECTED_ACCEPTANCE_AUDITOR_SHA}"
require_sha "${ROBUST_RUNNER_SOURCE}" "${EXPECTED_ROBUST_RUNNER_SHA}"
require_sha "${ROBUST_SELECTION_BUILDER_SOURCE}" "${EXPECTED_ROBUST_SELECTION_BUILDER_SHA}"
require_sha "${EXACT_REPEAT_BUILDER_SOURCE}" "${EXPECTED_EXACT_REPEAT_BUILDER_SHA}"
require_sha "${GATE_SOURCE}" "${EXPECTED_GATE_SHA}"
require_sha "${CONTROLLER_SOURCE}" "${EXPECTED_CONTROLLER_SHA}"
require_sha "${P}/run_job_round8_strict_serial.py" "${EXPECTED_BASE_RUNNER_SHA}"
require_sha "${T}/package/job_round8_strict_replay_contract.py" "${EXPECTED_CONTRACT_SHA}"

mkdir -p "${ROOT}"
env PYTHONPATH=/tmp python3 -u "${RECOVER}" \
  --state "${STATE}" \
  --expected-state-sha256 "${EXPECTED_STATE_SHA}" \
  --selection "${FIRST_ACCEPTANCE_SELECTION}" \
  --expected-selection-sha256 "${EXPECTED_ACCEPTANCE_SELECTION_SHA}" \
  --first-events "${FIRST_ACCEPTANCE_EVENTS}" \
  --expected-first-events-sha256 "${EXPECTED_FIRST_ACCEPTANCE_EVENTS_SHA}" \
  --formal-events "${FORMAL_ACCEPTANCE_EVENTS}" \
  --expected-formal-events-sha256 "${EXPECTED_FORMAL_ACCEPTANCE_EVENTS_SHA}" \
  --audit-root "${AUDIT_ROOT}" > "${ROOT}/recovery_dry_run.json"

env PYTHONPATH=/tmp python3 -u "${RECOVER}" \
  --state "${STATE}" \
  --expected-state-sha256 "${EXPECTED_STATE_SHA}" \
  --selection "${FIRST_ACCEPTANCE_SELECTION}" \
  --expected-selection-sha256 "${EXPECTED_ACCEPTANCE_SELECTION_SHA}" \
  --first-events "${FIRST_ACCEPTANCE_EVENTS}" \
  --expected-first-events-sha256 "${EXPECTED_FIRST_ACCEPTANCE_EVENTS_SHA}" \
  --formal-events "${FORMAL_ACCEPTANCE_EVENTS}" \
  --expected-formal-events-sha256 "${EXPECTED_FORMAL_ACCEPTANCE_EVENTS_SHA}" \
  --audit-root "${AUDIT_ROOT}" --apply > "${ROOT}/recovery_apply.json"

jq -e '
  .status == "applied_and_verified" and
  .accepted_count_preserved == 2 and
  .formal_replay_count_preserved == 191 and
  .global_ordinal_preserved == 336 and
  .incumbent_score_before == 4.540453524964638 and
  .incumbent_score_after == 4.501092144610971 and
  .physical_workload_calls_added == 0
' "${ROOT}/recovery_apply.json" >/dev/null
jq -e '
  .accepted_count == 2 and .accepted_target == 10 and
  .incumbent_score == 4.501092144610971 and
  .formal_replay_count == 191 and .global_ordinal == 336
' "${STATE}" >/dev/null
STATE_SHA_AFTER=$(sha "${STATE}")

mkdir -p "${PACKAGE}" "${SECOND}"
cp "${P}/run_job_round8_strict_serial.py" "${PACKAGE}/run_job_round8_strict_serial_base_v6.py"
cp "${ROBUST_RUNNER_SOURCE}" "${PACKAGE}/run_job_round8_strict_serial.py"
cp "${T}/package/job_round8_strict_replay_contract.py" "${PACKAGE}/job_round8_strict_replay_contract.py"
cp "${ROBUST_SELECTION_BUILDER_SOURCE}" "${PACKAGE}/build_job_three_observation_formal_selection_v1.py"
cp "${EXACT_REPEAT_BUILDER_SOURCE}" "${PACKAGE}/build_job_exact_plan_repeat_validation.py"
cp "${GATE_SOURCE}" "${PACKAGE}/evaluate_job_clean5_gate.py"
cp "${CONTROLLER_SOURCE}" "${PACKAGE}/job_formal_target10_batch_controller.py"

require_sha "${PACKAGE}/run_job_round8_strict_serial_base_v6.py" "${EXPECTED_BASE_RUNNER_SHA}"
require_sha "${PACKAGE}/run_job_round8_strict_serial.py" "${EXPECTED_ROBUST_RUNNER_SHA}"
require_sha "${PACKAGE}/job_round8_strict_replay_contract.py" "${EXPECTED_CONTRACT_SHA}"
require_sha "${PACKAGE}/build_job_three_observation_formal_selection_v1.py" "${EXPECTED_ROBUST_SELECTION_BUILDER_SHA}"
require_sha "${PACKAGE}/build_job_exact_plan_repeat_validation.py" "${EXPECTED_EXACT_REPEAT_BUILDER_SHA}"
require_sha "${PACKAGE}/evaluate_job_clean5_gate.py" "${EXPECTED_GATE_SHA}"
require_sha "${PACKAGE}/job_formal_target10_batch_controller.py" "${EXPECTED_CONTROLLER_SHA}"

python3 -u "${PACKAGE}/build_job_exact_plan_repeat_validation.py" \
  --selection "${FIRST_DIAGNOSTIC_SELECTION}" \
  --summary "${FIRST_DIAGNOSTIC_SUMMARY}" \
  --output "${SECOND}/top5.json"
python3 -u "${PACKAGE}/build_job_exact_plan_repeat_validation.py" \
  --selection "${FIRST_DIAGNOSTIC_SELECTION}" \
  --summary "${FIRST_DIAGNOSTIC_SUMMARY}" \
  --output "${SECOND}/top5.second.json"
cmp "${SECOND}/top5.json" "${SECOND}/top5.second.json"

python3 -u "${PACKAGE}/run_job_round8_strict_serial.py" \
  "${SECOND}/top5.json" "${SECOND}/replay" \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --state "${STATE}" --accepted-target 10 \
  --diagnostic-only --dry-run > "${SECOND}/dry_run.json"
test "$(sha "${STATE}")" = "${STATE_SHA_AFTER}"

python3 -u "${PACKAGE}/run_job_round8_strict_serial.py" \
  "${SECOND}/top5.json" "${SECOND}/replay" \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --state "${STATE}" --accepted-target 10 \
  --diagnostic-only
validate_clean5 "${SECOND}/replay/summary.json"
test "$(sha "${STATE}")" = "${STATE_SHA_AFTER}"

gate_rc=0
python3 -u "${PACKAGE}/evaluate_job_clean5_gate.py" \
  --selection "${SECOND}/top5.json" \
  --summary "${SECOND}/replay/summary.json" \
  --output "${SECOND}/entry_gate_raw.json" || gate_rc=$?
test "${gate_rc}" = 0
jq -e '.passed == true and ([.checks[]] | all)' "${SECOND}/entry_gate_raw.json" >/dev/null

GLOBAL_BEFORE=$(tr -d '[:space:]' < "${GLOBAL_COUNTER}")
FORMAL_REPLAY_BEFORE=$(tr -d '[:space:]' < "${FORMAL_REPLAY_COUNTER}")
ACCEPTED_BEFORE=$(tr -d '[:space:]' < "${ACCEPTED_COUNTER}")
test "${ACCEPTED_BEFORE}" = 2
jq \
  --arg state_sha "${STATE_SHA_AFTER}" \
  --argjson global_before "${GLOBAL_BEFORE}" \
  --argjson replay_before "${FORMAL_REPLAY_BEFORE}" \
  --argjson accepted_before "${ACCEPTED_BEFORE}" '
  .formal_state_sha256_before = $state_sha |
  .formal_state_sha256_after = $state_sha |
  .formal_counter_pins_before_start = {
    "global_ordinal": $global_before,
    "formal_search_replay_count": $replay_before,
    "formal_search_accepted_count": $accepted_before
  } |
  .formal_accepted_total = $accepted_before |
  .accepted_target = 10 |
  .formal_admission_contract =
    "three_independent_exact_point_median_acceptance_v2"
' "${SECOND}/entry_gate_raw.json" > "${SECOND}/entry_gate.json"

mkdir -p "${FORMAL}/package"
python3 -u "${PACKAGE}/build_job_three_observation_formal_selection_v1.py" \
  --first-selection "${FIRST_DIAGNOSTIC_SELECTION}" \
  --first-summary "${FIRST_DIAGNOSTIC_SUMMARY}" \
  --second-selection "${SECOND}/top5.json" \
  --second-summary "${SECOND}/replay/summary.json" \
  --gate "${SECOND}/entry_gate.json" \
  --output "${FORMAL}/package/top5.json"

SELECTION_SHA=$(sha "${FORMAL}/package/top5.json")
GATE_SHA_FINAL=$(sha "${SECOND}/entry_gate.json")
python3 -u "${PACKAGE}/run_job_round8_strict_serial.py" \
  "${FORMAL}/package/top5.json" "${FORMAL}/replay" \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --state "${STATE}" --accepted-target 10 \
  --dry-run > "${FORMAL}/package/dry_run.json"
test "$(sha "${STATE}")" = "${STATE_SHA_AFTER}"

cd "${PACKAGE}"
python3 -u "${PACKAGE}/job_formal_target10_batch_controller.py" \
  --selection "${FORMAL}/package/top5.json" \
  --selection-sha256 "${SELECTION_SHA}" \
  --gate "${SECOND}/entry_gate.json" \
  --gate-sha256 "${GATE_SHA_FINAL}" \
  --state "${STATE}" --state-sha256 "${STATE_SHA_AFTER}" \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --replay-root "${FORMAL}/replay" \
  --global-counter "${GLOBAL_COUNTER}" \
  --formal-replay-counter "${FORMAL_REPLAY_COUNTER}" \
  --accepted-counter "${ACCEPTED_COUNTER}" \
  > "${FORMAL}/controller_result.json"

jq -e '
  .baseline_params_restored == true and
  .physical_invocation_count <= 5 and
  .technical_failure_count >= 0 and
  .three_observation_acceptance_contract ==
    "job_three_independent_exact_point_median_acceptance_v2"
' "${FORMAL}/replay/summary.json" >/dev/null
jq -n \
  --arg completed_at "$(date -Iseconds)" \
  --arg state_sha256 "$(sha "${STATE}")" \
  --argjson accepted_count "$(jq '.accepted_count' "${STATE}")" \
  --argjson diagnostic_physical 5 \
  --argjson formal_physical "$(jq '.physical_invocation_count' "${FORMAL}/replay/summary.json")" \
  '{
    status: "complete",
    completed_at: $completed_at,
    state_sha256: $state_sha256,
    accepted_count: $accepted_count,
    diagnostic_physical_calls: $diagnostic_physical,
    formal_physical_calls: $formal_physical,
    acceptance_contract: "job_three_independent_exact_point_median_acceptance_v2"
  }' > "${ROOT}/completion_audit.json"

echo "JOB_ROBUST_V8_CYCLE_COMPLETE accepted=$(jq -r '.accepted_count' "${STATE}")"
