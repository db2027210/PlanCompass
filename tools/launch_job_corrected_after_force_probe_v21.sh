#!/usr/bin/env bash
set -euo pipefail

# Wait for a sealed all-probe diagnostic batch, validate its state neutrality,
# and use those observations to generate and replay a fresh diagnostic 3+2
# Top-5.  This script never enables formal state updates.

B="/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
P="${B}/job_round8_corrected_top5_v6_20260731"
SOURCE="${JOB_SOURCE:-${B}/job_batch14_force_probe_after_no_positive_v20_20260731T1340}"
TARGET="${JOB_TARGET:-${B}/job_batch15_corrected_after_force_probe_v21_20260731T1400}"
SOURCE_PID="${JOB_SOURCE_PID:-73385}"
GENERATOR="/tmp/job_exact_regime_successor_expand_v14.py"
RUNNER="${P}/run_job_round8_strict_serial.py"
STATE="${P}/authoritative_formal_state_snapshot.json"
LOCK="${TARGET}.controller.lock"
LOG="${TARGET}/controller.log"

EXPECTED_GENERATOR_SHA="e05adba25c82faf41b606d19a1b82180a25b392225da6fbd4ebcdae981dd4876"
EXPECTED_SOURCE_LAUNCHER_SHA="890c59cedf38b05b23db379722425d84d0a419d737f352a987f2e6828eb09e23"
EXPECTED_STATE_SHA="430e874c54603aff85304d8e4f52e28d4d6784236978e5e6b33962403c87c2a6"

mkdir -p "${TARGET}"
exec 9>"${LOCK}"
if ! flock -n 9; then
    echo "JOB corrected-after-force-probe controller lock is already held" >&2
    exit 73
fi
exec > >(tee -a "${LOG}") 2>&1

test "$(sha256sum /tmp/job_force_probe_after_no_positive_v20.sh | awk '{print $1}')" = \
    "${EXPECTED_SOURCE_LAUNCHER_SHA}"
echo "waiting for force-probe controller pid=${SOURCE_PID}"
while kill -0 "${SOURCE_PID}" 2>/dev/null; do
    sleep 10
done

SOURCE_SELECTION="${SOURCE}/top5.json"
SOURCE_SELECTION_SHA="${SOURCE}/selection.sha256"
SOURCE_EVENTS="${SOURCE}/replay/events.jsonl"
SOURCE_SUMMARY="${SOURCE}/replay/summary.json"
test -f "${SOURCE_SELECTION}"
test -f "${SOURCE_SELECTION_SHA}"
test -f "${SOURCE_EVENTS}"
test -f "${SOURCE_SUMMARY}"

test "$(sha256sum "${GENERATOR}" | awk '{print $1}')" = "${EXPECTED_GENERATOR_SHA}"
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
sha256sum -c "${SOURCE_SELECTION_SHA}"

jq -e '
    .diagnostic_only == true and
    .diagnostic_role_evaluation_only == false and
    .trusted_count == 0 and
    .probe_count == 5 and
    .novel_top5_fingerprint_count == 5 and
    (.top5 | length) == 5 and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    ([.top5[] | select(.selection_role == "probe")] | length) == 5
' "${SOURCE_SELECTION}" >/dev/null

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

test "$(
    jq -s '
        [
            .[] |
            select(
                .event == "job_round8_candidate_replay_complete" and
                .technical_failure == false
            )
        ] | length
    ' "${SOURCE_EVENTS}"
)" = "5"

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
    --exclude "${B}/job_batch14_force_probe_after_no_positive_v20_20260731T1340/top5.json" \
    --mode diagnostic \
    --output "${SELECTION}"

jq -e '
    .diagnostic_only == true and
    .diagnostic_role_evaluation_only == true and
    .trusted_count == 3 and
    .probe_count == 2 and
    .novel_top5_fingerprint_count == 5 and
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
