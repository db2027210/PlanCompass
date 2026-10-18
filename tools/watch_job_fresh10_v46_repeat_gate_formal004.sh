#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
P=${B}/job_round8_corrected_top5_v6_20260731
ROOT=${T}/autonomous_v45
SOURCE=${ROOT}/diagnostic_probe004
BUILDER=/tmp/build_job_exact_plan_repeat_validation.py
GATE_TOOL=/tmp/evaluate_job_clean5_gate.py
BASE_RUNNER=${P}/run_job_round8_strict_serial.py
ROBUST_RUNNER_SOURCE=/tmp/run_job_round8_strict_serial_robust_v7.py
ROBUST_SELECTION_BUILDER=/tmp/build_job_three_observation_formal_selection_v1.py
CONTROLLER_SOURCE=/tmp/job_formal_target10_batch_controller.py
CONTRACT_SOURCE=${T}/package/job_round8_strict_replay_contract.py
STATE=${T}/package/formal_state.json
EXPECTED_STATE_SHA=be1b8fecccd9a1092b0d49c7bfb76b86f5d61fab5031387169250b5380606ef4

test "$(sha256sum "${BUILDER}" | awk '{print $1}')" = 89c3a2238830087c129cadee76cd5a28248d87ce10b0fd98c6d5f9cb62bdd6cf
test "$(sha256sum "${GATE_TOOL}" | awk '{print $1}')" = 72700f5169ca7ba3bd1b527397ac179036ab8bebebb5fb7e977f5be4de2b558e
test "$(sha256sum "${BASE_RUNNER}" | awk '{print $1}')" = 9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
test "$(sha256sum "${ROBUST_RUNNER_SOURCE}" | awk '{print $1}')" = 9251d7fa68276113656826bd6df1777c7b7dfe71b42d012674fbd19ca4b2f09c
test "$(sha256sum "${ROBUST_SELECTION_BUILDER}" | awk '{print $1}')" = 40c528bb43ecd6b31987daa6d0dec2704fde93da11d510261d854d9eceb532a7
test "$(sha256sum "${CONTROLLER_SOURCE}" | awk '{print $1}')" = df512fdecaa2d98f23144991a75ed4eb68c32ec5cc143ff9d9bfb1232f179356
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"

validate_clean_diagnostic() {
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
  ' "${summary}" >/dev/null || return 1
  test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}" || return 1
}

validate_state_neutral_diagnostic() {
  local summary=$1
  jq -e '
    .diagnostic_only == true and
    (.physical_invocation_count >= 0 and .physical_invocation_count <= 5) and
    (.successful_real_replay_count >= 0) and
    (.technical_failure_count >= 0) and
    (.successful_real_replay_count + .technical_failure_count
      == .physical_invocation_count) and
    .baseline_params_restored == true and
    .baseline_restore.success == true and
    .initial_state == .final_state and
    .formal_update_flags == {
      "accepted": false,
      "gradient": false,
      "incumbent": false,
      "range": false
    }
  ' "${summary}" >/dev/null || return 1
  test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}" || return 1
}

test -f "${SOURCE}/completion_audit.json"
jq -e '
  .status == "clean5_state_neutral" and
  .physical_calls == 5 and
  .valid_replays == 5 and
  .technical_failures == 0 and
  .accepted_added == 0
' "${SOURCE}/completion_audit.json" >/dev/null
validate_clean_diagnostic "${SOURCE}/replay/summary.json"
PASSED_SOURCE=
PASSED_GATE=
FIRST_SOURCE=

for ordinal in 1 2 3 4 5; do
  TARGET=${ROOT}/diagnostic_repeat004_${ordinal}
  mkdir -p "${TARGET}"
  exec 8>"${TARGET}.lock"
  flock -n 8
  test ! -e "${TARGET}/top5.json"
  test ! -e "${TARGET}/replay"

  python3 -u "${BUILDER}" \
    --selection "${SOURCE}/top5.json" \
    --summary "${SOURCE}/replay/summary.json" \
    --output "${TARGET}/top5.json"
  python3 -u "${BUILDER}" \
    --selection "${SOURCE}/top5.json" \
    --summary "${SOURCE}/replay/summary.json" \
    --output "${TARGET}/top5.second.json"
  cmp "${TARGET}/top5.json" "${TARGET}/top5.second.json"

  jq -e '
    .diagnostic_only == true and
    .trusted_count == 3 and .probe_count == 2 and
    .novel_top5_fingerprint_count == 5 and
    (.top5 | length) == 5 and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    ([.top5[] | select(.selection_role == "trusted")] | length) == 3 and
    ([.top5[] | select(.selection_role == "probe")] | length) == 2 and
    all(.top5[];
      .boundary_corner == false and
      .three_level_fingerprint.valid == true and
      .accepted == false and
      .gradient_update_applied == false and
      .incumbent_update_applied == false and
      .range_update_applied == false and
      has("feature_ratio") and has("predicted_resource") and
      has("utilization") and has("dScore/dU") and has("T") and
      has("confidence") and has("g_j") and has("eta") and
      has("delta_param") and has("candidate_score"))
  ' "${TARGET}/top5.json" >/dev/null

  python3 -u "${BASE_RUNNER}" \
    "${TARGET}/top5.json" "${TARGET}/replay" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --state "${STATE}" --accepted-target 10 \
    --diagnostic-only --dry-run > "${TARGET}/dry_run.json"
  test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
  python3 -u "${BASE_RUNNER}" \
    "${TARGET}/top5.json" "${TARGET}/replay" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --state "${STATE}" --accepted-target 10 \
    --diagnostic-only
  if ! validate_clean_diagnostic "${TARGET}/replay/summary.json"; then
    # A technical failure consumes this batch's physical budget but supplies
    # no calibration evidence.  Verify full state neutrality, retain the last
    # clean source, and use the next independently capped diagnostic batch.
    validate_state_neutral_diagnostic "${TARGET}/replay/summary.json"
    continue
  fi

  gate_rc=0
  python3 -u "${GATE_TOOL}" \
    --selection "${TARGET}/top5.json" \
    --summary "${TARGET}/replay/summary.json" \
    --output "${TARGET}/entry_gate_raw.json" || gate_rc=$?
  if test "${gate_rc}" = 0 && jq -e '.passed == true and ([.checks[]] | all)' "${TARGET}/entry_gate_raw.json" >/dev/null; then
    FIRST_SOURCE=${SOURCE}
    PASSED_SOURCE=${TARGET}
    PASSED_GATE=${TARGET}/entry_gate_raw.json
    break
  fi
  SOURCE=${TARGET}
done

test -n "${PASSED_SOURCE}"
FORMAL=${ROOT}/formal_batch004
PACKAGE=${FORMAL}/package
REPLAY=${FORMAL}/replay
mkdir -p "${PACKAGE}"
test ! -e "${REPLAY}"
cp "${CONTROLLER_SOURCE}" "${PACKAGE}/job_formal_target10_batch_controller.py"
cp "${BASE_RUNNER}" "${PACKAGE}/run_job_round8_strict_serial_base_v6.py"
cp "${ROBUST_RUNNER_SOURCE}" "${PACKAGE}/run_job_round8_strict_serial.py"
cp "${ROBUST_SELECTION_BUILDER}" "${PACKAGE}/build_job_three_observation_formal_selection_v1.py"
cp "${CONTRACT_SOURCE}" "${PACKAGE}/job_round8_strict_replay_contract.py"

GLOBAL_BEFORE=$(tr -d '[:space:]' < "${B}/replay_counter.txt")
FORMAL_REPLAY_BEFORE=$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")
ACCEPTED_BEFORE=$(tr -d '[:space:]' < "${B}/formal_search_accepted_counter.txt")
test "${ACCEPTED_BEFORE}" = 2
jq \
  --arg state_sha "${EXPECTED_STATE_SHA}" \
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
  .formal_admission_contract =
    "three_independent_exact_point_median_acceptance_v2" |
  .formal_expected_improvement_required = false
' "${PASSED_GATE}" > "${PACKAGE}/entry_gate.json"

test -n "${FIRST_SOURCE}"
python3 -u "${PACKAGE}/build_job_three_observation_formal_selection_v1.py" \
  --first-selection "${FIRST_SOURCE}/top5.json" \
  --first-summary "${FIRST_SOURCE}/replay/summary.json" \
  --second-selection "${PASSED_SOURCE}/top5.json" \
  --second-summary "${PASSED_SOURCE}/replay/summary.json" \
  --gate "${PACKAGE}/entry_gate.json" \
  --output "${PACKAGE}/top5_promoted_formal.json"

SELECTION=${PACKAGE}/top5_promoted_formal.json
SELECTION_SHA=$(sha256sum "${SELECTION}" | awk '{print $1}')
GATE_SHA=$(sha256sum "${PACKAGE}/entry_gate.json" | awk '{print $1}')
python3 -u "${PACKAGE}/run_job_round8_strict_serial.py" \
  "${SELECTION}" "${REPLAY}" \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --state "${STATE}" --accepted-target 10 \
  --dry-run > "${PACKAGE}/dry_run.json"
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"

cd "${PACKAGE}"
exec python3 -u "${PACKAGE}/job_formal_target10_batch_controller.py" \
  --selection "${SELECTION}" --selection-sha256 "${SELECTION_SHA}" \
  --gate "${PACKAGE}/entry_gate.json" --gate-sha256 "${GATE_SHA}" \
  --state "${STATE}" --state-sha256 "${EXPECTED_STATE_SHA}" \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --replay-root "${REPLAY}" \
  --global-counter "${B}/replay_counter.txt" \
  --formal-replay-counter "${B}/formal_search_replay_counter.txt" \
  --accepted-counter "${B}/formal_search_accepted_counter.txt"
