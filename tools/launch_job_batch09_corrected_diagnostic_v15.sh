#!/usr/bin/env bash
set -euo pipefail

# Remote node02 launcher.  It waits for the currently running all-probe
# diagnostic batch, validates that the batch was clean and state-neutral, then
# performs EXPLAIN-only successor generation followed by one strict serial
# diagnostic Top-5 replay.  It never enables formal state updates.

B="/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
P="${B}/job_round8_corrected_top5_v6_20260731"
SOURCE="${B}/job_batch08_diagnostic_force_probe_v14_20260731T1120"
TARGET="${B}/job_batch09_corrected_top5_v15_20260731T1135"
GENERATOR="/tmp/job_exact_regime_successor_expand_v14.py"
RUNNER="${P}/run_job_round8_strict_serial.py"
STATE="${P}/authoritative_formal_state_snapshot.json"
LOCK="${B}/job_batch09_corrected_top5_v15.lock"
LOG="${TARGET}/controller.log"

EXPECTED_GENERATOR_SHA="e05adba25c82faf41b606d19a1b82180a25b392225da6fbd4ebcdae981dd4876"
EXPECTED_SOURCE_SELECTION_SHA="9f36c54be14ca45d9127a0e5d8b633ccda0159eedc33517b157795b782c39252"
EXPECTED_STATE_SHA="430e874c54603aff85304d8e4f52e28d4d6784236978e5e6b33962403c87c2a6"
SOURCE_PID="71277"

mkdir -p "${TARGET}"
exec 9>"${LOCK}"
if ! flock -n 9; then
    echo "batch09 controller lock is already held" >&2
    exit 73
fi
exec > >(tee -a "${LOG}") 2>&1

echo "waiting for batch08 pid=${SOURCE_PID}" 
while kill -0 "${SOURCE_PID}" 2>/dev/null; do
    sleep 10
done

SOURCE_SUMMARY="${SOURCE}/replay/summary.json"
SOURCE_EVENTS="${SOURCE}/replay/events.jsonl"
SOURCE_SELECTION="${SOURCE}/top5.json"
test -f "${SOURCE_SUMMARY}"
test -f "${SOURCE_EVENTS}"
test -f "${SOURCE_SELECTION}"

test "$(sha256sum "${GENERATOR}" | awk '{print $1}')" = "${EXPECTED_GENERATOR_SHA}"
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
