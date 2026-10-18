#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
P=${B}/job_round8_corrected_top5_v6_20260731
SOURCE=${B}/job_batch20_trusted_validation_v28_20260731T1715
FAILED_SUCCESSOR=${B}/job_batch21_corrected_validation_v30_20260731T1755
TARGET=${B}/job_batch22_exact_plan_repeat_v31_20260731T1820
BUILDER=/tmp/build_job_exact_plan_repeat_validation.py
RUNNER=${P}/run_job_round8_strict_serial.py
STATE=${P}/authoritative_formal_state_snapshot.json
LOCK=${TARGET}.controller.lock

EXPECTED_BUILDER_SHA=89c3a2238830087c129cadee76cd5a28248d87ce10b0fd98c6d5f9cb62bdd6cf
EXPECTED_SOURCE_SELECTION_SHA=8e6fb7815d0f600db67167438f5624e429ceaffaf93cde5012c7742fdf867ff8
EXPECTED_SOURCE_SUMMARY_SHA=c5ebf5113235ac138fc483aab6e22113661fa57b9fe35e273fdb82a2d12c3bc6
EXPECTED_RUNNER_SHA=9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
EXPECTED_STATE_SHA=430e874c54603aff85304d8e4f52e28d4d6784236978e5e6b33962403c87c2a6

mkdir -p "${TARGET}"
exec 9>"${LOCK}"
if ! flock -n 9; then
    echo "JOB batch22 controller lock is already held" >&2
    exit 73
fi
exec > >(tee -a "${TARGET}/controller.log") 2>&1

test "$(sha256sum "${BUILDER}" | awk '{print $1}')" = "${EXPECTED_BUILDER_SHA}"
test "$(sha256sum "${SOURCE}/top5.json" | awk '{print $1}')" = "${EXPECTED_SOURCE_SELECTION_SHA}"
test "$(sha256sum "${SOURCE}/replay/summary.json" | awk '{print $1}')" = "${EXPECTED_SOURCE_SUMMARY_SHA}"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"

# The failed successor was EXPLAIN-only and must not have consumed a call.
jq -e '
    .new_workload_calls == 0 and
    .trusted_count == 0 and
    .probe_count == 5 and
    (.top5 | length) == 5
' "${FAILED_SUCCESSOR}/top5.json" >/dev/null
test ! -e "${FAILED_SUCCESSOR}/replay"

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
' "${SOURCE}/replay/summary.json" >/dev/null

SELECTION=${TARGET}/top5.json
SECOND=${TARGET}/top5.second.json
python3 -u "${BUILDER}" \
    --selection "${SOURCE}/top5.json" \
    --summary "${SOURCE}/replay/summary.json" \
    --output "${SELECTION}"
python3 -u "${BUILDER}" \
    --selection "${SOURCE}/top5.json" \
    --summary "${SOURCE}/replay/summary.json" \
    --output "${SECOND}"
cmp "${SELECTION}" "${SECOND}"

jq -e '
    .successor_generation_contract == "job_exact_plan_repeat_validation_v1" and
    .diagnostic_only == true and
    .diagnostic_role_evaluation_only == true and
    .trusted_count == 3 and
    .probe_count == 2 and
    .novel_top5_fingerprint_count == 5 and
    (.top5 | length) == 5 and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    ([.top5[].candidate_params | tojson] | unique | length) == 5 and
    ([.top5[] | select(.selection_role == "trusted")] | length) == 3 and
    ([.top5[] | select(.selection_role == "probe")] | length) == 2 and
    all(
        .top5[];
        .boundary_corner == false and
        .three_level_fingerprint.valid == true and
        .accepted == false and
        .gradient_update_applied == false and
        .incumbent_update_applied == false and
        .range_update_applied == false and
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
sha256sum "${SELECTION}" "${BUILDER}" "${RUNNER}" "${STATE}" > "${TARGET}/sealed.sha256"

python3 -u "${RUNNER}" \
    "${SELECTION}" "${TARGET}/replay" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --state "${STATE}" \
    --accepted-target 10 \
    --diagnostic-only \
    --dry-run > "${TARGET}/dry_run.json"

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
    host="127.0.0.1", port=64309, user="postgres", password="CHANGE_ME",
    dbname="imdb_job", connect_timeout=5,
)
cursor = connection.cursor()
cursor.execute("select 1")
assert cursor.fetchone() == (1,)
connection.close()
print(json.dumps({"status": "ready", "workload_calls": 0}))
' > "${TARGET}/pre_workload_readiness.json"

test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
exec python3 -u "${RUNNER}" \
    "${SELECTION}" "${TARGET}/replay" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --state "${STATE}" \
    --accepted-target 10 \
    --diagnostic-only
