#!/usr/bin/env bash
set -euo pipefail

# node02 handover: wait for the sealed JOB diagnostic validation batch,
# evaluate only the fixed formal-entry contract, and replay the very same
# sealed Top-5 in formal mode.  No additional predicted-improvement threshold
# is allowed here: acceptance/rejection is decided by the real formal replay.

B="${JOB_BASE:-/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726}"
P="${JOB_AUTHORITY:-${B}/job_round8_corrected_top5_v6_20260731}"
SOURCE="${JOB_SOURCE:-${B}/job_batch20_trusted_validation_v28_20260731T1715}"
TARGET="${JOB_TARGET:-${B}/formal10_round8_autonomous_batch05_promoted_gate_v29_20260731T1740}"
PACKAGE="${TARGET}/package"
REPLAY="${TARGET}/replay"
STATE="${P}/authoritative_formal_state_snapshot.json"
SOURCE_SELECTION="${SOURCE}/top5.json"
SOURCE_EVENTS="${SOURCE}/replay/events.jsonl"
SOURCE_SUMMARY="${SOURCE}/replay/summary.json"
GATE_TOOL="/tmp/analyze_top5_validation_gate_v1.py"
RUNNER="${P}/run_job_round8_strict_serial.py"
TEMPLATE_PACKAGE="${JOB_TEMPLATE_PACKAGE:-${B}/formal10_round8_autonomous_batch05_promoted_gate_v29_20260731T1740/package}"
CONTROLLER="${PACKAGE}/job_formal_target10_batch_controller.py"
GLOBAL_COUNTER="${B}/replay_counter.txt"
FORMAL_REPLAY_COUNTER="${B}/formal_search_replay_counter.txt"
ACCEPTED_COUNTER="${B}/formal_search_accepted_counter.txt"
SOURCE_CONTROLLER_PID="${JOB_SOURCE_CONTROLLER_PID:-73116}"
LOCK="${JOB_PROMOTION_LOCK:-${TARGET}.promotion.lock}"
EXPECTED_SELECTION_SHA="${JOB_EXPECTED_SELECTION_SHA:-8e6fb7815d0f600db67167438f5624e429ceaffaf93cde5012c7742fdf867ff8}"
EXPECTED_GATE_TOOL_SHA="49936cbc4319ef6055d6653c276892cd9df408a98d8560460c3e4f0bd7e37d30"
EXPECTED_RUNNER_SHA="9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1"
EXPECTED_CONTROLLER_SHA="0181f503eb601ff42e37ac188b6e71cbb70b367a35a2300c05bda778a911a164"
EXPECTED_STATE_SHA="430e874c54603aff85304d8e4f52e28d4d6784236978e5e6b33962403c87c2a6"

mkdir -p "${PACKAGE}"
test "$(sha256sum "${TEMPLATE_PACKAGE}/job_formal_target10_batch_controller.py" | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA}"
test "$(sha256sum "${TEMPLATE_PACKAGE}/run_job_round8_strict_serial.py" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
cp "${TEMPLATE_PACKAGE}/job_formal_target10_batch_controller.py" "${CONTROLLER}"
cp "${TEMPLATE_PACKAGE}/run_job_round8_strict_serial.py" "${PACKAGE}/run_job_round8_strict_serial.py"
test "$(sha256sum "${CONTROLLER}" | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA}"
test "$(sha256sum "${PACKAGE}/run_job_round8_strict_serial.py" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
exec 9>"${LOCK}"
if ! flock -n 9; then
    echo "JOB batch20 promotion watcher lock is already held" >&2
    exit 73
fi
exec > >(tee -a "${TARGET}/controller.log") 2>&1

echo "waiting for JOB diagnostic controller pid=${SOURCE_CONTROLLER_PID}"
while kill -0 "${SOURCE_CONTROLLER_PID}" 2>/dev/null; do
    sleep 10
done

test -f "${SOURCE_SELECTION}"
test -f "${SOURCE_EVENTS}"
test -f "${SOURCE_SUMMARY}"
test "$(sha256sum "${SOURCE_SELECTION}" | awk '{print $1}')" = "${EXPECTED_SELECTION_SHA}"
test "$(sha256sum "${GATE_TOOL}" | awk '{print $1}')" = "${EXPECTED_GATE_TOOL_SHA}"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"

jq -e '
    .diagnostic_only == true and
    .physical_invocation_count == 5 and
    .real_workload_invocation_count == 5 and
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

RAW_GATE="${PACKAGE}/entry_gate_raw.json"
GATE="${PACKAGE}/entry_gate.json"
python3 -u "${GATE_TOOL}" \
    --selection "${SOURCE_SELECTION}" \
    --observations "${SOURCE_EVENTS}" \
    --event-name job_round8_candidate_replay_complete \
    --output "${RAW_GATE}" >/dev/null

GLOBAL_BEFORE="$(tr -d '[:space:]' < "${GLOBAL_COUNTER}")"
FORMAL_REPLAY_BEFORE="$(tr -d '[:space:]' < "${FORMAL_REPLAY_COUNTER}")"
ACCEPTED_BEFORE="$(tr -d '[:space:]' < "${ACCEPTED_COUNTER}")"
case "${GLOBAL_BEFORE}:${FORMAL_REPLAY_BEFORE}:${ACCEPTED_BEFORE}" in
    *[!0-9:]*|*::*|:*|*:) echo "invalid JOB formal counter pin" >&2; exit 74 ;;
esac
test "${ACCEPTED_BEFORE}" = "2"

jq \
    --arg state_sha "${EXPECTED_STATE_SHA}" \
    --argjson global_before "${GLOBAL_BEFORE}" \
    --argjson replay_before "${FORMAL_REPLAY_BEFORE}" \
    --argjson accepted_before "${ACCEPTED_BEFORE}" \
    --slurpfile source_selection "${SOURCE_SELECTION}" \
    '
    .checks.baseline_restored = true |
    .checks.formal_state_unchanged = true |
    .checks.five_novel_noncorner_fingerprints = (
        ([$source_selection[0].top5[] | select(.boundary_corner != false)] | length) == 0 and
        ([$source_selection[0].top5[].three_level_fingerprint.id] | unique | length) == 5
    ) |
    .baseline_restored = true |
    .formal_state_sha256_before = $state_sha |
    .formal_state_sha256_after = $state_sha |
    .formal_counter_pins_before_start = {
        "global_ordinal": $global_before,
        "formal_search_replay_count": $replay_before,
        "formal_search_accepted_count": $accepted_before
    } |
    .formal_accepted_total = $accepted_before |
    .formal_admission_contract = "diagnostic_quality_gate_passed_then_real_replay_accept_or_reject_v1" |
    .formal_expected_improvement_required = false |
    .passed = ([.checks[]] | all)
    ' "${RAW_GATE}" > "${GATE}.tmp"
mv "${GATE}.tmp" "${GATE}"
jq -e '.passed == true' "${GATE}" >/dev/null

# Promotion changes execution policy only.  Candidate parameters, ordering,
# predictions, fingerprints and roles remain byte-for-byte equivalent values.
SELECTION="${PACKAGE}/top5_promoted_formal.json"
jq --arg source_sha "${EXPECTED_SELECTION_SHA}" '
    .diagnostic_only = false |
    .diagnostic_role_evaluation_only = false |
    .formal_admission_contract = "diagnostic_quality_gate_passed_then_real_replay_accept_or_reject_v1" |
    .formal_expected_improvement_required = false |
    .promoted_from_diagnostic_selection_sha256 = $source_sha |
    .top5 |= map(
        .diagnostic_only = false |
        if .selection_role == "trusted" then
            .used_for_gradient = true |
            .used_for_inner_incumbent_update = true
        else
            .used_for_gradient = false |
            .used_for_inner_incumbent_update = false
        end
    )
    ' "${SOURCE_SELECTION}" > "${SELECTION}.tmp"
mv "${SELECTION}.tmp" "${SELECTION}"

jq -e '
    .diagnostic_only == false and
    .formal_expected_improvement_required == false and
    .trusted_count == 3 and
    .probe_count == 2 and
    .novel_top5_fingerprint_count == 5 and
    (.top5 | length) == 5 and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    ([.top5[] | select(.selection_role == "trusted" and .used_for_gradient == true and .used_for_inner_incumbent_update == true)] | length) == 3 and
    ([.top5[] | select(.selection_role == "probe" and .used_for_gradient == false and .used_for_inner_incumbent_update == false)] | length) == 2 and
    all(
        .top5[];
        .boundary_corner == false and
        .three_level_fingerprint.valid == true and
        has("feature_ratio") and
        has("predicted_resource") and
        has("utilization") and
        has("dScore/dU") and
        has("T") and
        has("confidence") and
        has("g_j") and
        has("eta") and
        has("delta_param") and
        has("candidate_score")
    )
' "${SELECTION}" >/dev/null

SELECTION_SHA="$(sha256sum "${SELECTION}" | awk '{print $1}')"
GATE_SHA="$(sha256sum "${GATE}" | awk '{print $1}')"
STATE_SHA="$(sha256sum "${STATE}" | awk '{print $1}')"
test "${STATE_SHA}" = "${EXPECTED_STATE_SHA}"
sha256sum "${SOURCE_SELECTION}" "${SELECTION}" "${GATE}" "${CONTROLLER}" "${PACKAGE}/run_job_round8_strict_serial.py" > "${PACKAGE}/sealed.sha256"

python3 -u "${RUNNER}" \
    "${SELECTION}" "${REPLAY}" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --state "${STATE}" \
    --accepted-target 10 \
    --dry-run > "${PACKAGE}/dry_run.json"

cd "${PACKAGE}"
exec python3 -u "${CONTROLLER}" \
    --selection "${SELECTION}" \
    --selection-sha256 "${SELECTION_SHA}" \
    --gate "${GATE}" \
    --gate-sha256 "${GATE_SHA}" \
    --state "${STATE}" \
    --state-sha256 "${STATE_SHA}" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --replay-root "${REPLAY}" \
    --global-counter "${GLOBAL_COUNTER}" \
    --formal-replay-counter "${FORMAL_REPLAY_COUNTER}" \
    --accepted-counter "${ACCEPTED_COUNTER}"
