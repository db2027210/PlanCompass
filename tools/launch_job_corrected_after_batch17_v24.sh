#!/usr/bin/env bash
set -euo pipefail

# Wait for the state-neutral five-probe batch17 to seal, then generate and
# execute one fresh diagnostic 3-trusted + 2-probe Top-5.  This controller
# never enables accepted/incumbent/range/gradient updates.

B="/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
P="${B}/job_round8_corrected_top5_v6_20260731"
SOURCE="${JOB_SOURCE:-${B}/job_batch17_bridge_recovered_lcb_probe_v23_20260731T1438}"
TARGET="${JOB_TARGET:-${B}/job_batch18_corrected_after_batch17_v24_20260731T1500}"
GENERATOR="/tmp/job_exact_regime_successor_expand_lcb_v15.py"
RUNNER="${P}/run_job_round8_strict_serial.py"
STATE="${P}/authoritative_formal_state_snapshot.json"
LOCK="${TARGET}.controller.lock"
LOG="${TARGET}/controller.log"

EXPECTED_GENERATOR_SHA="a03c2264cdfe2b26a8bb4a5bcbc5648498ce68020806d6d4a84e3cb592b51da5"
EXPECTED_RUNNER_SHA="9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1"
EXPECTED_STATE_SHA="430e874c54603aff85304d8e4f52e28d4d6784236978e5e6b33962403c87c2a6"

mkdir -p "${TARGET}"
exec 9>"${LOCK}"
if ! flock -n 9; then
    echo "JOB batch18 controller lock is already held" >&2
    exit 73
fi
exec > >(tee -a "${LOG}") 2>&1

SOURCE_SELECTION="${SOURCE}/top5.json"
SOURCE_SELECTION_SHA="${SOURCE}/selection.sha256"
SOURCE_EVENTS="${SOURCE}/replay/events.jsonl"
SOURCE_SUMMARY="${SOURCE}/replay/summary.json"

for path in \
    "${SOURCE_SELECTION}" \
    "${SOURCE_SELECTION_SHA}" \
    "${SOURCE_EVENTS}"; do
    test -f "${path}"
done

test "$(sha256sum "${GENERATOR}" | awk '{print $1}')" = "${EXPECTED_GENERATOR_SHA}"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
sha256sum -c "${SOURCE_SELECTION_SHA}"

jq -e '
    .diagnostic_only == true and
    .trusted_count == 0 and
    .probe_count == 5 and
    .novel_top5_fingerprint_count == 5 and
    (.top5 | length) == 5 and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    all(
        .top5[];
        .selection_role == "probe" and
        .boundary_corner == false and
        .three_level_fingerprint.valid == true
    )
' "${SOURCE_SELECTION}" >/dev/null

# Waiting performs no workload call.  The source runner owns the only active
# JOB replay lock and writes summary.json only after baseline restoration.
while ! test -f "${SOURCE_SUMMARY}"; do
    sleep 10
done

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
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"

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
    --exclude "${B}/job_batch16_lcb_probe_discovery_v22_20260731T1425/top5.json" \
    --exclude "${SOURCE_SELECTION}" \
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
        has("predicted_resource_interval") and
        has("predicted_score_interval") and
        has("formal_lcb_score") and
        has("predicted_improvement_lower_bound") and
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
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"

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

# Zero-call readiness uses the same libpq path as the point runner.  It fails
# before reservation when the published PostgreSQL endpoint is unavailable.
test "$(
    docker inspect ra_pg5_job_node02_v1_outercontrol \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}'
)" = "healthy"
PYTHONPATH="/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/runtime/python/lib/python3.11/site-packages" \
    /mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/runtime/python/bin/python3 \
    -c '
import json
import psycopg2

connection = psycopg2.connect(
    host="127.0.0.1",
    port=64309,
    user="postgres",
    password="CHANGE_ME",
    dbname="imdb_job",
    connect_timeout=5,
)
cursor = connection.cursor()
cursor.execute("select 1")
assert cursor.fetchone() == (1,)
connection.close()
print(json.dumps({"status": "ready", "workload_calls": 0}))
' > "${TARGET}/pre_workload_readiness.json"

test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
python3 -u "${RUNNER}" \
    "${SELECTION}" \
    "${TARGET}/replay" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --state "${STATE}" \
    --accepted-target 10 \
    --diagnostic-only
