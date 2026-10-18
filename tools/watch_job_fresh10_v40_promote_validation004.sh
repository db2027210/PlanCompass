#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
P=${B}/job_round8_corrected_top5_v6_20260731
SOURCE=${T}/diagnostic_validation_batch004
TARGET=${T}/formal_batch002_promoted_validation004
PACKAGE=${TARGET}/package
REPLAY=${TARGET}/replay
STATE=${T}/package/formal_state.json
SOURCE_PID_FILE=${SOURCE}/runner.pid
SOURCE_SELECTION=${SOURCE}/top5.json
SOURCE_SUMMARY=${SOURCE}/replay/summary.json
GATE_TOOL=/tmp/evaluate_job_clean5_gate.py
CONTROLLER_SOURCE=/tmp/job_formal_target10_batch_controller.py
RUNNER_SOURCE=${P}/run_job_round8_strict_serial.py
CONTROLLER=${PACKAGE}/job_formal_target10_batch_controller.py
RUNNER=${PACKAGE}/run_job_round8_strict_serial.py
GLOBAL_COUNTER=${B}/replay_counter.txt
FORMAL_REPLAY_COUNTER=${B}/formal_search_replay_counter.txt
ACCEPTED_COUNTER=${B}/formal_search_accepted_counter.txt

EXPECTED_SOURCE_SELECTION_SHA=4bbce78a99ab601d53ed87834d2d2b2ccdc4f7f550e4be7dac5d9832515937ad
EXPECTED_GATE_TOOL_SHA=72700f5169ca7ba3bd1b527397ac179036ab8bebebb5fb7e977f5be4de2b558e
EXPECTED_CONTROLLER_SHA=df512fdecaa2d98f23144991a75ed4eb68c32ec5cc143ff9d9bfb1232f179356
EXPECTED_RUNNER_SHA=9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
EXPECTED_STATE_SHA=4d18a6e5b4049f5890d0ac3602f3a3bfdba38a39db5d22c819734130575c601b

test "$(sha256sum "${GATE_TOOL}" | awk '{print $1}')" = "${EXPECTED_GATE_TOOL_SHA}"
test "$(sha256sum "${CONTROLLER_SOURCE}" | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA}"
test "$(sha256sum "${RUNNER_SOURCE}" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
mkdir -p "${PACKAGE}"
exec 9>"${TARGET}.promotion.lock"
flock -n 9
exec > >(tee -a "${TARGET}/controller.log") 2>&1
cp "${CONTROLLER_SOURCE}" "${CONTROLLER}"
cp "${RUNNER_SOURCE}" "${RUNNER}"

for _ in $(seq 1 60); do
  test -s "${SOURCE_PID_FILE}" && break
  sleep 2
done
test -s "${SOURCE_PID_FILE}"
SOURCE_PID=$(tr -d '[:space:]' < "${SOURCE_PID_FILE}")
case "${SOURCE_PID}" in *[!0-9]*|'') echo "invalid source pid" >&2; exit 72 ;; esac
while kill -0 "${SOURCE_PID}" 2>/dev/null; do
  sleep 10
done

test -f "${SOURCE_SELECTION}"
test -f "${SOURCE_SUMMARY}"
test "$(sha256sum "${SOURCE_SELECTION}" | awk '{print $1}')" = "${EXPECTED_SOURCE_SELECTION_SHA}"
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
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
' "${SOURCE_SUMMARY}" >/dev/null

RAW_GATE=${PACKAGE}/entry_gate_raw.json
GATE=${PACKAGE}/entry_gate.json
python3 -u "${GATE_TOOL}" \
  --selection "${SOURCE_SELECTION}" \
  --summary "${SOURCE_SUMMARY}" \
  --output "${RAW_GATE}" || gate_rc=$?
gate_rc=${gate_rc:-0}

GLOBAL_BEFORE=$(tr -d '[:space:]' < "${GLOBAL_COUNTER}")
FORMAL_REPLAY_BEFORE=$(tr -d '[:space:]' < "${FORMAL_REPLAY_COUNTER}")
ACCEPTED_BEFORE=$(tr -d '[:space:]' < "${ACCEPTED_COUNTER}")
case "${GLOBAL_BEFORE}:${FORMAL_REPLAY_BEFORE}:${ACCEPTED_BEFORE}" in
  *[!0-9:]*|*::*|:*|*:) echo "invalid JOB counter pin" >&2; exit 74 ;;
esac
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
' "${RAW_GATE}" > "${GATE}.tmp"
mv "${GATE}.tmp" "${GATE}"
test "${gate_rc}" = 0
jq -e '.passed == true and ([.checks[]] | all)' "${GATE}" >/dev/null

SELECTION=${PACKAGE}/top5_promoted_formal.json
jq --arg source_sha "${EXPECTED_SOURCE_SELECTION_SHA}" --slurpfile gate "${GATE}" '
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
' "${SOURCE_SELECTION}" > "${SELECTION}.tmp"
mv "${SELECTION}.tmp" "${SELECTION}"

SELECTION_SHA=$(sha256sum "${SELECTION}" | awk '{print $1}')
GATE_SHA=$(sha256sum "${GATE}" | awk '{print $1}')
STATE_SHA=$(sha256sum "${STATE}" | awk '{print $1}')
test "${STATE_SHA}" = "${EXPECTED_STATE_SHA}"
sha256sum "${SOURCE_SELECTION}" "${SOURCE_SUMMARY}" "${SELECTION}" \
  "${GATE}" "${CONTROLLER}" "${RUNNER}" > "${PACKAGE}/sealed.sha256"

python3 -u "${RUNNER_SOURCE}" \
  "${SELECTION}" "${REPLAY}" \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --state "${STATE}" --accepted-target 10 \
  --dry-run > "${PACKAGE}/dry_run.json"

cd "${PACKAGE}"
exec python3 -u "${CONTROLLER}" \
  --selection "${SELECTION}" --selection-sha256 "${SELECTION_SHA}" \
  --gate "${GATE}" --gate-sha256 "${GATE_SHA}" \
  --state "${STATE}" --state-sha256 "${STATE_SHA}" \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --replay-root "${REPLAY}" \
  --global-counter "${GLOBAL_COUNTER}" \
  --formal-replay-counter "${FORMAL_REPLAY_COUNTER}" \
  --accepted-counter "${ACCEPTED_COUNTER}"
