#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
P=${B}/job_round8_corrected_top5_v6_20260731
ROOT=${T}/autonomous_v43
SOURCE=${ROOT}/diagnostic_probe003
SOURCE_PID=79846
BUILDER=/tmp/build_job_exact_plan_repeat_validation.py
GATE_TOOL=/tmp/evaluate_job_clean5_gate.py
RUNNER=${P}/run_job_round8_strict_serial.py
CONTROLLER_SOURCE=/tmp/job_formal_target10_batch_controller.py
CONTRACT_SOURCE=${T}/package/job_round8_strict_replay_contract.py
STATE=${T}/package/formal_state.json
EXPECTED_STATE_SHA=7fde8e94b9ee76a07f2e7759404e7ed560973dd0f9ef6b23c89de7802c087276

test "$(sha256sum "${BUILDER}" | awk '{print $1}')" = 89c3a2238830087c129cadee76cd5a28248d87ce10b0fd98c6d5f9cb62bdd6cf
test "$(sha256sum "${GATE_TOOL}" | awk '{print $1}')" = 72700f5169ca7ba3bd1b527397ac179036ab8bebebb5fb7e977f5be4de2b558e
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = 9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
test "$(sha256sum "${CONTROLLER_SOURCE}" | awk '{print $1}')" = df512fdecaa2d98f23144991a75ed4eb68c32ec5cc143ff9d9bfb1232f179356
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"

while kill -0 "${SOURCE_PID}" 2>/dev/null; do
  sleep 10
done

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
  ' "${summary}" >/dev/null
  test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
}

validate_clean_diagnostic "${SOURCE}/replay/summary.json"
PASSED_SOURCE=
PASSED_GATE=

for ordinal in 1 2 3 4 5; do
  TARGET=${ROOT}/diagnostic_repeat003_${ordinal}
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

  python3 -u "${RUNNER}" \
    "${TARGET}/top5.json" "${TARGET}/replay" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --state "${STATE}" --accepted-target 10 \
    --diagnostic-only --dry-run > "${TARGET}/dry_run.json"
  test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
  python3 -u "${RUNNER}" \
    "${TARGET}/top5.json" "${TARGET}/replay" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --state "${STATE}" --accepted-target 10 \
    --diagnostic-only
  validate_clean_diagnostic "${TARGET}/replay/summary.json"

  gate_rc=0
  python3 -u "${GATE_TOOL}" \
    --selection "${TARGET}/top5.json" \
    --summary "${TARGET}/replay/summary.json" \
    --output "${TARGET}/entry_gate_raw.json" || gate_rc=$?
  if test "${gate_rc}" = 0 && jq -e '.passed == true and ([.checks[]] | all)' "${TARGET}/entry_gate_raw.json" >/dev/null; then
    PASSED_SOURCE=${TARGET}
    PASSED_GATE=${TARGET}/entry_gate_raw.json
    break
  fi
  SOURCE=${TARGET}
done

test -n "${PASSED_SOURCE}"
FORMAL=${ROOT}/formal_batch003
PACKAGE=${FORMAL}/package
REPLAY=${FORMAL}/replay
mkdir -p "${PACKAGE}"
test ! -e "${REPLAY}"
cp "${CONTROLLER_SOURCE}" "${PACKAGE}/job_formal_target10_batch_controller.py"
cp "${RUNNER}" "${PACKAGE}/run_job_round8_strict_serial.py"
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
    "diagnostic_quality_gate_passed_then_real_replay_accept_or_reject_v1" |
  .formal_expected_improvement_required = false
' "${PASSED_GATE}" > "${PACKAGE}/entry_gate.json"

SOURCE_SELECTION=${PASSED_SOURCE}/top5.json
SOURCE_SHA=$(sha256sum "${SOURCE_SELECTION}" | awk '{print $1}')
jq --arg source_sha "${SOURCE_SHA}" --slurpfile gate "${PACKAGE}/entry_gate.json" '
  .diagnostic_only = false |
  .diagnostic_role_evaluation_only = false |
  .formal_admission_contract =
    "diagnostic_quality_gate_passed_then_real_replay_accept_or_reject_v1" |
  .formal_expected_improvement_required = false |
  .gradient_rank_update_allowed = $gate[0].gradient_rank_update_allowed |
  .promoted_from_diagnostic_selection_sha256 = $source_sha |
  .top5 |= map(
    .diagnostic_only = false |
    if .selection_role == "trusted" then
      .used_for_gradient = $gate[0].gradient_rank_update_allowed |
      .used_for_inner_incumbent_update = true
    else
      .used_for_gradient = false |
      .used_for_inner_incumbent_update = false
    end
  )
' "${SOURCE_SELECTION}" > "${PACKAGE}/top5_promoted_formal.json"

SELECTION=${PACKAGE}/top5_promoted_formal.json
SELECTION_SHA=$(sha256sum "${SELECTION}" | awk '{print $1}')
GATE_SHA=$(sha256sum "${PACKAGE}/entry_gate.json" | awk '{print $1}')
python3 -u "${RUNNER}" \
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
