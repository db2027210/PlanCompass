#!/usr/bin/env bash
set -euo pipefail

# Remote node02 continuation.  Wait for the corrected diagnostic Top-5,
# evaluate the complete formal-entry gate, and launch a formal Top-5 only when
# both the gate and the positive predicted-improvement check pass.

B="/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
P="${B}/job_round8_corrected_top5_v6_20260731"
SOURCE="${JOB_SOURCE:-${B}/job_batch09_corrected_top5_v15_20260731T1135}"
TARGET="${JOB_TARGET:-${B}/job_batch10_formal_after_gate_v16_20260731T1210}"
GENERATOR="/tmp/job_exact_regime_successor_expand_v14.py"
GATE_TOOL="/tmp/analyze_top5_validation_gate_v1.py"
RUNNER="${P}/run_job_round8_strict_serial.py"
STATE="${P}/authoritative_formal_state_snapshot.json"
LOCK="${B}/job_batch10_gate_and_formal_v16.lock"
LOG="${TARGET}/controller.log"

EXPECTED_GENERATOR_SHA="e05adba25c82faf41b606d19a1b82180a25b392225da6fbd4ebcdae981dd4876"
EXPECTED_GATE_TOOL_SHA="49936cbc4319ef6055d6653c276892cd9df408a98d8560460c3e4f0bd7e37d30"
EXPECTED_SOURCE_SELECTION_SHA="${JOB_SOURCE_SELECTION_SHA:-60e471a34e56ef87886ed986609678c8d6187052e6681200f3ee6618e0ad6069}"
EXPECTED_STATE_SHA="430e874c54603aff85304d8e4f52e28d4d6784236978e5e6b33962403c87c2a6"
SOURCE_CONTROLLER_PID="${JOB_SOURCE_CONTROLLER_PID:-85210}"

mkdir -p "${TARGET}"
exec 9>"${LOCK}"
if ! flock -n 9; then
    echo "batch10 controller lock is already held" >&2
    exit 73
fi
exec > >(tee -a "${LOG}") 2>&1

echo "waiting for batch09 controller pid=${SOURCE_CONTROLLER_PID}"
while kill -0 "${SOURCE_CONTROLLER_PID}" 2>/dev/null; do
    sleep 10
done

SOURCE_SELECTION="${SOURCE}/top5.json"
SOURCE_EVENTS="${SOURCE}/replay/events.jsonl"
SOURCE_SUMMARY="${SOURCE}/replay/summary.json"
test -f "${SOURCE_SELECTION}"
test -f "${SOURCE_EVENTS}"
test -f "${SOURCE_SUMMARY}"

test "$(sha256sum "${GENERATOR}" | awk '{print $1}')" = "${EXPECTED_GENERATOR_SHA}"
test "$(sha256sum "${GATE_TOOL}" | awk '{print $1}')" = "${EXPECTED_GATE_TOOL_SHA}"
test "$(sha256sum "${SOURCE_SELECTION}" | awk '{print $1}')" = "${EXPECTED_SOURCE_SELECTION_SHA}"
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

RAW_GATE="${TARGET}/entry_gate_raw.json"
FINAL_GATE="${TARGET}/entry_gate.json"
python3 -u "${GATE_TOOL}" \
    --selection "${SOURCE_SELECTION}" \
    --observations "${SOURCE_EVENTS}" \
    --event-name job_round8_candidate_replay_complete \
    --output "${RAW_GATE}" >/dev/null

jq \
    --arg state_sha "${EXPECTED_STATE_SHA}" \
    --slurpfile source_selection "${SOURCE_SELECTION}" \
    '
    .checks.baseline_restored = true |
    .checks.formal_state_unchanged = true |
    .checks.five_novel_noncorner_fingerprints = (
        ([
            $source_selection[0].top5[] |
            select(.boundary_corner != false)
        ] | length) == 0 and
        ([
            $source_selection[0].top5[].three_level_fingerprint.id
        ] | unique | length) == 5
    ) |
    .baseline_restored = true |
    .formal_state_sha256_before = $state_sha |
    .formal_state_sha256_after = $state_sha |
    .passed = ([.checks[]] | all)
    ' "${RAW_GATE}" > "${FINAL_GATE}.tmp"
mv "${FINAL_GATE}.tmp" "${FINAL_GATE}"
jq -e '.passed == true' "${FINAL_GATE}" >/dev/null

SELECTION="${TARGET}/top5.json"
set +e
python3 -u "${GENERATOR}" \
    --history "${P}/authority/ranking_analysis.json" \
    --evidence-top5 "${SOURCE_SELECTION}" \
    --events "${SOURCE_EVENTS}" \
    --formal-state "${STATE}" \
    --incumbent-events "${B}/formal10_round8_autonomous_batch01_v1_20260731T0302/replay/events.jsonl" \
    --config "${B}/gradient_debug_round7_fresh_exclusion_v1/config.json" \
    --capture "${B}/job_batch02_exact_regime_calibration_v1_20260731T0332/baseline_plan_capture.json" \
    --incumbent-point-result "${B}/formal_points/point_0238/point_result.json" \
    --code-root "${B}/gradient_debug_round7_fresh_exclusion_v1/code" \
    --exclude "${P}/round7_replayed_fingerprints.json" \
    --exclude "${B}/job_batch03_dynamic_incumbent_successor_v3_20260731T0610/top5.json" \
    --exclude "${B}/job_batch04_unknown_regime_probe5_v1_20260731T0655/top5.json" \
    --exclude "${B}/job_batch05_exact_regime_validation_v1_20260731T0737/top5.json" \
    --exclude "${B}/job_batch06_exact_regime_successor_v1_20260731T0808/top5.json" \
    --exclude "${B}/job_batch07_diagnostic_probe_discovery_v13_20260731T1018/top5.json" \
    --exclude "${B}/job_batch08_diagnostic_force_probe_v14_20260731T1120/top5.json" \
    --exclude "${B}/job_batch09_corrected_top5_v15_20260731T1135/top5.json" \
    --mode formal \
    --minimum-predicted-improvement 0.005 \
    --output "${SELECTION}"
GENERATOR_RC=$?
set -e
if [ "${GENERATOR_RC}" -ne 0 ]; then
    printf '%s\n' "${GENERATOR_RC}" > "${TARGET}/formal_generation_returncode.txt"
    echo "formal generation failed closed; no workload launched"
    exit "${GENERATOR_RC}"
fi

jq -e '
    .diagnostic_only == false and
    .diagnostic_role_evaluation_only == false and
    .trusted_count == 3 and
    .probe_count == 2 and
    .novel_top5_fingerprint_count == 5 and
    .formal_acquisition.mode == "formal" and
    .formal_acquisition.expected_improvement > 0.005 and
    (.top5 | length) == 5 and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    ([.top5[] | select(.selection_role == "trusted")] | length) == 3 and
    ([.top5[] | select(.selection_role == "probe")] | length) == 2 and
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

sha256sum "${FINAL_GATE}" > "${TARGET}/entry_gate.sha256"
sha256sum "${SELECTION}" > "${TARGET}/selection.sha256"

python3 -u "${RUNNER}" \
    "${SELECTION}" \
    "${TARGET}/replay" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --state "${STATE}" \
    --accepted-target 10 \
    --dry-run > "${TARGET}/dry_run.json"

python3 -u "${RUNNER}" \
    "${SELECTION}" \
    "${TARGET}/replay" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --state "${STATE}" \
    --accepted-target 10
