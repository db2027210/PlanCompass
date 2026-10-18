#!/usr/bin/env bash
set -euo pipefail

JOB_BASE=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
JOB_AUTH=${JOB_BASE}/job_round8_corrected_top5_v6_20260731
JOB_EVIDENCE=${JOB_BASE}/job_batch19_probe_reserve_v27_20260731T1635
JOB_PREVIOUS=${JOB_BASE}/job_batch18_corrected_after_batch17_v24_20260731T1500
JOB_TARGET=${JOB_BASE}/job_batch20_trusted_validation_v28_20260731T1715
JOB_GENERATOR=/tmp/job_exact_regime_successor_expand_lcb_v16.py
JOB_RUNNER=${JOB_AUTH}/run_job_round8_strict_serial.py
JOB_STATE=${JOB_AUTH}/authoritative_formal_state_snapshot.json
JOB_LOCK=${JOB_TARGET}.controller.lock
JOB_LOG=${JOB_TARGET}/controller.log

JOB_EXPECTED_GENERATOR_SHA=88782634e4bc3fcb71368ca5e176b32446695165f79c7ce0906040c2e22a9859
JOB_EXPECTED_RUNNER_SHA=9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
JOB_EXPECTED_STATE_SHA=430e874c54603aff85304d8e4f52e28d4d6784236978e5e6b33962403c87c2a6
JOB_EXPECTED_EVIDENCE_SUMMARY_SHA=00b1e226ce19c952db836a0c97ba4a53929b4bce6c1c7280e5ee6c5934403b1b
JOB_EXPECTED_EVIDENCE_TOP5_SHA=360caf8558c4ddd6bd9b0f96d50c4467a613a699aa6aa7e3749d0fac78c0a25d

mkdir -p "${JOB_TARGET}"
exec 9>"${JOB_LOCK}"
if ! flock -n 9; then
    echo "JOB batch20 controller lock is already held" >&2
    exit 73
fi
exec > >(tee -a "${JOB_LOG}") 2>&1

test "$(sha256sum "${JOB_GENERATOR}" | awk '{print $1}')" = \
    "${JOB_EXPECTED_GENERATOR_SHA}"
test "$(sha256sum "${JOB_RUNNER}" | awk '{print $1}')" = \
    "${JOB_EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${JOB_STATE}" | awk '{print $1}')" = \
    "${JOB_EXPECTED_STATE_SHA}"
test "$(sha256sum "${JOB_EVIDENCE}/replay/summary.json" | awk '{print $1}')" = \
    "${JOB_EXPECTED_EVIDENCE_SUMMARY_SHA}"
test "$(sha256sum "${JOB_EVIDENCE}/top5.json" | awk '{print $1}')" = \
    "${JOB_EXPECTED_EVIDENCE_TOP5_SHA}"
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
' "${JOB_EVIDENCE}/replay/summary.json" >/dev/null

JOB_SELECTION=${JOB_TARGET}/top5.json
python3 -u "${JOB_GENERATOR}" \
    --history "${JOB_AUTH}/authority/ranking_analysis.json" \
    --evidence-top5 "${JOB_EVIDENCE}/top5.json" \
    --events "${JOB_EVIDENCE}/replay/events.jsonl" \
    --formal-state "${JOB_STATE}" \
    --incumbent-events "${JOB_BASE}/formal10_round8_autonomous_batch01_v1_20260731T0302/replay/events.jsonl" \
    --config "${JOB_BASE}/gradient_debug_round7_fresh_exclusion_v1/config.json" \
    --capture "${JOB_BASE}/job_batch02_exact_regime_calibration_v1_20260731T0332/baseline_plan_capture.json" \
    --incumbent-point-result "${JOB_BASE}/formal_points/point_0238/point_result.json" \
    --code-root "${JOB_BASE}/gradient_debug_round7_fresh_exclusion_v1/code" \
    --exclude "${JOB_AUTH}/round7_replayed_fingerprints.json" \
    --exclude "${JOB_BASE}/job_batch03_dynamic_incumbent_successor_v3_20260731T0610/top5.json" \
    --exclude "${JOB_BASE}/job_batch04_unknown_regime_probe5_v1_20260731T0655/top5.json" \
    --exclude "${JOB_BASE}/job_batch05_exact_regime_validation_v1_20260731T0737/top5.json" \
    --exclude "${JOB_BASE}/job_batch06_exact_regime_successor_v1_20260731T0808/top5.json" \
    --exclude "${JOB_BASE}/job_batch07_diagnostic_probe_discovery_v13_20260731T1018/top5.json" \
    --exclude "${JOB_BASE}/job_batch08_diagnostic_force_probe_v14_20260731T1120/top5.json" \
    --exclude "${JOB_BASE}/job_batch09_corrected_top5_v15_20260731T1135/top5.json" \
    --exclude "${JOB_BASE}/job_batch11_corrected_after_probe_v17_20260731T1235/top5.json" \
    --exclude "${JOB_BASE}/job_batch14_force_probe_after_no_positive_v20_20260731T1340/top5.json" \
    --exclude "${JOB_BASE}/job_batch16_lcb_probe_discovery_v22_20260731T1425/top5.json" \
    --exclude "${JOB_BASE}/job_batch17_bridge_recovered_lcb_probe_v23_20260731T1438/top5.json" \
    --exclude "${JOB_PREVIOUS}/top5.json" \
    --exclude "${JOB_EVIDENCE}/top5.json" \
    --mode diagnostic \
    --output "${JOB_SELECTION}"

jq -e '
    .successor_generation_contract ==
        "job_exact_regime_guarded_local_ratio_successor_top5_v2" and
    .diagnostic_only == true and
    .diagnostic_role_evaluation_only == true and
    .formal_acquisition.diagnostic_probe_discovery == false and
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
' "${JOB_SELECTION}" >/dev/null

sha256sum "${JOB_SELECTION}" > "${JOB_TARGET}/selection.sha256"
test "$(sha256sum "${JOB_STATE}" | awk '{print $1}')" = \
    "${JOB_EXPECTED_STATE_SHA}"

python3 -u "${JOB_RUNNER}" \
    "${JOB_SELECTION}" \
    "${JOB_TARGET}/replay" \
    --authority-root "${JOB_AUTH}/authority" \
    --observations "${JOB_AUTH}/job_round8_v6_observations.json" \
    --exclusion "${JOB_AUTH}/round7_replayed_fingerprints.json" \
    --state "${JOB_STATE}" \
    --accepted-target 10 \
    --diagnostic-only \
    --dry-run > "${JOB_TARGET}/dry_run.json"

test "$(
    docker inspect ra_pg5_job_node02_v1_outercontrol \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}'
)" = healthy
PYTHONPATH=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/runtime/python/lib/python3.11/site-packages \
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
' > "${JOB_TARGET}/pre_workload_readiness.json"

test "$(sha256sum "${JOB_STATE}" | awk '{print $1}')" = \
    "${JOB_EXPECTED_STATE_SHA}"
python3 -u "${JOB_RUNNER}" \
    "${JOB_SELECTION}" \
    "${JOB_TARGET}/replay" \
    --authority-root "${JOB_AUTH}/authority" \
    --observations "${JOB_AUTH}/job_round8_v6_observations.json" \
    --exclusion "${JOB_AUTH}/round7_replayed_fingerprints.json" \
    --state "${JOB_STATE}" \
    --accepted-target 10 \
    --diagnostic-only
