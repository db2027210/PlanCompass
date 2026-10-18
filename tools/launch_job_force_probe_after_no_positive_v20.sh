#!/usr/bin/env bash
set -euo pipefail

# Run one state-neutral five-probe discovery batch after a clean diagnostic
# gate passes but formal successor generation finds no sufficiently positive
# trusted candidate.  No formal state update is enabled by this launcher.

B="/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
P="${B}/job_round8_corrected_top5_v6_20260731"
SOURCE="${JOB_SOURCE:-${B}/job_batch11_corrected_after_probe_v17_20260731T1235}"
FAILED_FORMAL="${JOB_FAILED_FORMAL:-${B}/job_batch12_formal_after_gate_v18_20260731T1300}"
TARGET="${JOB_TARGET:-${B}/job_batch14_force_probe_after_no_positive_v20_20260731T1340}"
GENERATOR="/tmp/job_exact_regime_successor_expand_v14.py"
RUNNER="${P}/run_job_round8_strict_serial.py"
STATE="${P}/authoritative_formal_state_snapshot.json"
LOCK="${TARGET}.controller.lock"
LOG="${TARGET}/controller.log"

EXPECTED_GENERATOR_SHA="e05adba25c82faf41b606d19a1b82180a25b392225da6fbd4ebcdae981dd4876"
EXPECTED_SOURCE_SELECTION_SHA="${JOB_SOURCE_SELECTION_SHA:-a0cbc2099e89ad34e1b3f09cf23933325bb1ec30c870d9972e52599ba7d04d20}"
EXPECTED_STATE_SHA="430e874c54603aff85304d8e4f52e28d4d6784236978e5e6b33962403c87c2a6"

mkdir -p "${TARGET}"
exec 9>"${LOCK}"
if ! flock -n 9; then
    echo "JOB force-probe controller lock is already held" >&2
    exit 73
fi
exec > >(tee -a "${LOG}") 2>&1

SOURCE_SELECTION="${SOURCE}/top5.json"
SOURCE_EVENTS="${SOURCE}/replay/events.jsonl"
SOURCE_SUMMARY="${SOURCE}/replay/summary.json"
ENTRY_GATE="${FAILED_FORMAL}/entry_gate.json"
FAILURE_AUDIT="${FAILED_FORMAL}/top5.json.failure_audit.json"
GENERATION_RC="${FAILED_FORMAL}/formal_generation_returncode.txt"

test -f "${SOURCE_SELECTION}"
test -f "${SOURCE_EVENTS}"
test -f "${SOURCE_SUMMARY}"
test -f "${ENTRY_GATE}"
test -f "${FAILURE_AUDIT}"
test -f "${GENERATION_RC}"

test "$(sha256sum "${GENERATOR}" | awk '{print $1}')" = "${EXPECTED_GENERATOR_SHA}"
test "$(sha256sum "${SOURCE_SELECTION}" | awk '{print $1}')" = "${EXPECTED_SOURCE_SELECTION_SHA}"
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
test "$(cat "${GENERATION_RC}")" = "1"

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

jq -e '
    .passed == true and
    ([.checks[]] | all) and
    .checks.baseline_restored == true and
    .checks.formal_state_unchanged == true
' "${ENTRY_GATE}" >/dev/null

jq -e '
    .status == "fail_closed" and
    .reason == "no_positive_trusted_improvement" and
    .recommended_next_mode == "diagnostic_force_probe_discovery" and
    .new_workload_calls == 0 and
    .required_score > .incumbent_score and
    .best_trusted_predicted_score <= .required_score
' "${FAILURE_AUDIT}" >/dev/null

SELECTION="${TARGET}/top5.json"
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
    --exclude "${B}/job_batch11_corrected_after_probe_v17_20260731T1235/top5.json" \
    --mode diagnostic \
    --force-probe-discovery \
    --output "${SELECTION}"

jq -e '
    .diagnostic_only == true and
    .diagnostic_role_evaluation_only == false and
    .formal_acquisition.mode == "diagnostic" and
    .trusted_count == 0 and
    .probe_count == 5 and
    .novel_top5_fingerprint_count == 5 and
    (.top5 | length) == 5 and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    ([.top5[] | select(.selection_role == "probe")] | length) == 5 and
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

sha256sum "${ENTRY_GATE}" > "${TARGET}/source_entry_gate.sha256"
sha256sum "${FAILURE_AUDIT}" > "${TARGET}/source_failure_audit.sha256"
sha256sum "${SELECTION}" > "${TARGET}/selection.sha256"

python3 -u "${RUNNER}" \
    "${SELECTION}" \
    "${TARGET}/replay" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --state "${STATE}" \
    --accepted-target 10 \
    --diagnostic-only \
    --dry-run > "${TARGET}/dry_run.json"

python3 -u "${RUNNER}" \
    "${SELECTION}" \
    "${TARGET}/replay" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --state "${STATE}" \
    --accepted-target 10 \
    --diagnostic-only
