#!/usr/bin/env bash
set -euo pipefail

# node01: convert a clean autonomous probe5 batch into an independent
# exact-plan repeat-validation trusted3+probe2 batch.  This is deterministic,
# state-neutral, and performs no candidate acceptance.

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
CURRENT_PACKAGE=${BASE}/gradient_debug_round49_stability_successor_v2_20260731T1745_package
CURRENT_RUN=${BASE}/gradient_debug_round49_stability_successor_v2_20260731T1745_run
PACKAGE=${BASE}/gradient_debug_round50_exact_plan_repeat_v4_20260731T1810_package
OUTPUT=${BASE}/gradient_debug_round50_exact_plan_repeat_v4_20260731T1810_run
FORMAL=${BASE}/formal10_exact_plan_regime_round28_feedback_v1_20260729T2055_run/formal_state.json
BASELINE=${BASE}/candidate_measurements/formal_point_20260729T050311Z_3000/summary.json
COUNTER=${BASE}/run/attempt48_resource_correction_22c1730m16r20w36w_rolling_v2.accepted_shrink.counter.txt
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
VENDOR=${BASE}/vendor/py311
BUILDER=/tmp/build_tpch_exact_plan_repeat_validation.py
CURRENT_PID=36330
CONTAINER=pg5_tpch_ap_slot_node01_20260726
DATABASE=template_fullgrid_tpch_pg5
LOCK=${CURRENT_RUN}.repeat50_watcher.lock
LOG=${CURRENT_RUN}/repeat50_watcher.log

EXPECTED_CURRENT_SELECTION_SHA=1962aa46a313ffcabf189747ddbf2a0925cf8c406646b9f7450fca4b83597c8c
EXPECTED_BUILDER_SHA=484704afde8e35cc4561ec37d5ce37f163fe7d6c38769ced52cd57c957914fe7
EXPECTED_FORMAL_SHA=335308f7c19be9bf958b50041f61a8b33fbace17c5ec546942ad35d687f7ecb4
EXPECTED_BASELINE_SHA=50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67
EXPECTED_WORKLOAD_SHA=25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008
EXPECTED_DRIVER_SHA=31c0080ec1487d0f4cb7a840c2584d890c5e780f561f6c4a480099ba029d80eb

exec 9>"${LOCK}"
if ! flock -n 9; then
    echo "TPC-H repeat50 watcher lock is already held" >&2
    exit 73
fi
exec > >(tee -a "${LOG}") 2>&1

test "$(sha256sum "${CURRENT_PACKAGE}/top5.json" | awk '{print $1}')" = "${EXPECTED_CURRENT_SELECTION_SHA}"
test "$(sha256sum "${BUILDER}" | awk '{print $1}')" = "${EXPECTED_BUILDER_SHA}"
test "$(sha256sum "${FORMAL}" | awk '{print $1}')" = "${EXPECTED_FORMAL_SHA}"
test "$(sha256sum "${BASELINE}" | awk '{print $1}')" = "${EXPECTED_BASELINE_SHA}"
test "$(tr -d '[:space:]' < "${COUNTER}")" = "1"
test ! -e "${PACKAGE}"
test ! -e "${OUTPUT}"

for _ in $(seq 1 720); do
    if ! kill -0 "${CURRENT_PID}" 2>/dev/null; then
        break
    fi
    COMMAND="$(ps -p "${CURRENT_PID}" -o args=)"
    case "${COMMAND}" in
        *gradient_debug_round49_stability_successor_v2_20260731T1745_package/run_tpch_round29_diagnostic_exact_plan_regime_batch.py*) ;;
        *) echo "TPC-H round49 PID command mismatch" >&2; exit 74 ;;
    esac
    sleep 10
done
if kill -0 "${CURRENT_PID}" 2>/dev/null; then
    echo "TPC-H round49 exceeded repeat watcher wait limit" >&2
    exit 75
fi

test -f "${CURRENT_RUN}/diagnostic_summary.json"
test -f "${CURRENT_RUN}/diagnostic_state.json"
jq -e '
    .status == "complete" and
    .physical_call_count == 5 and
    .valid_replay_count == 5 and
    .technical_failure_count == 0 and
    .baseline_restored == true and
    .diagnostic_only == true and
    .formal_state_mutation_allowed == false and
    .accepted_update_allowed == false and
    .incumbent_update_allowed == false and
    .range_update_allowed == false and
    .gradient_update_allowed == false
' "${CURRENT_RUN}/diagnostic_state.json" >/dev/null
test "$(sha256sum "${FORMAL}" | awk '{print $1}')" = "${EXPECTED_FORMAL_SHA}"

mkdir -p "${PACKAGE}"
"${PYTHON}" -u "${BUILDER}" \
    --selection "${CURRENT_PACKAGE}/top5.json" \
    --summary "${CURRENT_RUN}/diagnostic_summary.json" \
    --output "${PACKAGE}/top5_a.json"
"${PYTHON}" -u "${BUILDER}" \
    --selection "${CURRENT_PACKAGE}/top5.json" \
    --summary "${CURRENT_RUN}/diagnostic_summary.json" \
    --output "${PACKAGE}/top5_b.json"
cmp -s "${PACKAGE}/top5_a.json" "${PACKAGE}/top5_b.json"
mv "${PACKAGE}/top5_a.json" "${PACKAGE}/top5.json"

# Reuse only the already sealed runtime/config dependencies.  The repeat Top-5
# above is the sole changed algorithm artifact.
for name in \
    config.json gate.json resume_state.json \
    run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh \
    run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py \
    formal_measurement.py formal_contracts.py system_resource_monitor.py \
    run_tpch_round29_diagnostic_exact_plan_regime_batch.py \
    validate_tpch_round29_diagnostic_package.py \
    tpch_round19_plan_regime_select.py
do
    cp "${CURRENT_PACKAGE}/${name}" "${PACKAGE}/${name}"
done

jq -e '
    .diagnostic_only == true and
    .maximum_real_replay_invocations == 5 and
    .qps_used_for_inner_scoring == false and
    [.top5[].selection_role] == ["trusted", "trusted", "trusted", "probe", "probe"] and
    .novel_unique_fingerprint_count == 5 and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    ([.top5[].candidate_params] | unique | length) == 5 and
    all(
        .top5[];
        .boundary_corner == false and
        .boundary_plateau == false and
        .actual_parameter_noncorner_audit.reason == "actual_parameter_space_noncorner" and
        .accepted_update_allowed == false and
        .incumbent_update_allowed == false and
        .range_update_allowed == false and
        .gradient_update_allowed == false and
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
' "${PACKAGE}/top5.json" >/dev/null

SELECTION_SHA="$(sha256sum "${PACKAGE}/top5.json" | awk '{print $1}')"
CONFIG_SHA="$(sha256sum "${PACKAGE}/config.json" | awk '{print $1}')"
GATE_SHA="$(sha256sum "${PACKAGE}/gate.json" | awk '{print $1}')"
RESUME_SHA="$(sha256sum "${PACKAGE}/resume_state.json" | awk '{print $1}')"
WORKLOAD=${PACKAGE}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh
DRIVER=${PACKAGE}/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py
test "$(sha256sum "${WORKLOAD}" | awk '{print $1}')" = "${EXPECTED_WORKLOAD_SHA}"
test "$(sha256sum "${DRIVER}" | awk '{print $1}')" = "${EXPECTED_DRIVER_SHA}"

env -u LD_LIBRARY_PATH -u PYTHONHOME \
    PYTHONPATH="${PACKAGE}:${VENDOR}:${BASE}/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages" \
    PYTHONNOUSERSITE=1 \
    LD_PRELOAD=/usr/lib64/libstdc++.so.6 \
    "${PYTHON}" -u "${PACKAGE}/validate_tpch_round29_diagnostic_package.py" \
        --selection "${PACKAGE}/top5.json" \
        --resume-state "${PACKAGE}/resume_state.json" \
        --config "${PACKAGE}/config.json" \
        --gate "${PACKAGE}/gate.json" \
        --runner "${PACKAGE}/run_tpch_round29_diagnostic_exact_plan_regime_batch.py" \
        --workload "${WORKLOAD}" \
        --workload-driver "${DRIVER}" \
        --expected-workload-driver-sha256 "${EXPECTED_DRIVER_SHA}" \
        > "${PACKAGE}/preflight_validation.json"
jq -e '
    .status == "passed" and
    .roles == ["trusted", "trusted", "trusted", "probe", "probe"] and
    .unique_fingerprint_count == 5 and
    .unique_parameter_count == 5 and
    .maximum_physical_calls == 5
' "${PACKAGE}/preflight_validation.json" >/dev/null

test "$(${PYTHON} - <<'PY'
import hashlib,json
p="/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726/gradient_debug_round50_exact_plan_repeat_v4_20260731T1810_package/top5.json"
x=json.load(open(p))
print(len({c["three_level_fingerprint"]["id"] for c in x["top5"]}))
PY
)" = "5"
test "$(tr -d '[:space:]' < "${COUNTER}")" = "1"
test "$(sha256sum "${FORMAL}" | awk '{print $1}')" = "${EXPECTED_FORMAL_SHA}"
test "$(
    docker inspect -f '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.CpusetCpus}}' "${CONTAINER}"
)" = "true 22000000000 1730000000 1730000000 0-15,36-51"

sha256sum "${BUILDER}" "${PACKAGE}/top5.json" "${PACKAGE}/config.json" \
    "${PACKAGE}/resume_state.json" "${PACKAGE}/gate.json" "${WORKLOAD}" \
    "${DRIVER}" > "${PACKAGE}/sealed.sha256"

mkdir -p "${OUTPUT}"
nohup env -u LD_LIBRARY_PATH -u PYTHONHOME \
    PYTHONPATH="${PACKAGE}:${VENDOR}:${BASE}/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages" \
    PYTHONNOUSERSITE=1 \
    LD_PRELOAD=/usr/lib64/libstdc++.so.6 \
    PGPASSWORD=postgres PGHOST=127.0.0.1 PGPORT=63301 PGUSER=postgres \
    "${PYTHON}" -u "${PACKAGE}/run_tpch_round29_diagnostic_exact_plan_regime_batch.py" \
        --diagnostic-only \
        --selection "${PACKAGE}/top5.json" \
        --selection-sha256 "${SELECTION_SHA}" \
        --gate-summary "${PACKAGE}/gate.json" \
        --gate-summary-sha256 "${GATE_SHA}" \
        --baseline-summary "${BASELINE}" \
        --baseline-summary-sha256 "${EXPECTED_BASELINE_SHA}" \
        --config "${PACKAGE}/config.json" \
        --config-sha256 "${CONFIG_SHA}" \
        --workload "${WORKLOAD}" \
        --workload-sha256 "${EXPECTED_WORKLOAD_SHA}" \
        --accepted-counter "${COUNTER}" \
        --resume-state "${PACKAGE}/resume_state.json" \
        --resume-state-sha256 "${RESUME_SHA}" \
        --selection-start-rank 1 \
        --maximum-real-replay-invocations 5 \
        --output "${OUTPUT}" \
        --container "${CONTAINER}" \
        --database "${DATABASE}" \
        --timeout-seconds 900 > "${OUTPUT}/driver.log" 2>&1 &
NEXT_PID=$!
printf '%s\n' "${NEXT_PID}" > "${OUTPUT}/driver.pid"
sleep 5
kill -0 "${NEXT_PID}"
printf 'TPCH_REPEAT50_STARTED pid=%s selection_sha=%s\n' "${NEXT_PID}" "${SELECTION_SHA}"
