#!/usr/bin/env bash
set -euo pipefail

# node01: wait for the autonomous round49 successor, prove the complete
# entry gate, promote only execution-policy fields of its sealed Top-5, and
# start the first formal continuation batch.  Actual formal replay decides
# accept/reject; predicted improvement is intentionally not an entry gate.

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
PACKAGE=${BASE}/gradient_debug_round49_stability_successor_v2_20260731T1745_package
RUN=${BASE}/gradient_debug_round49_stability_successor_v2_20260731T1745_run
PREP=${BASE}/gradient_debug_round49_stability_successor_prep_v2_20260731T1745
FORMAL_STATE=${BASE}/formal10_exact_plan_regime_round28_feedback_v1_20260729T2055_run/formal_state.json
FORMAL_OUTPUT=${BASE}/formal10_exact_plan_regime_round49_gate_v3_20260731T1815_run
FORMAL_PACKAGE=${FORMAL_OUTPUT}/package
BASELINE=${BASE}/candidate_measurements/formal_point_20260729T050311Z_3000/summary.json
COUNTER=${BASE}/run/attempt48_resource_correction_22c1730m16r20w36w_rolling_v2.accepted_shrink.counter.txt
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
VENDOR=${BASE}/vendor/py311
ANALYZER=/tmp/analyze_top5_validation_gate_v2.py
GATE_BUILDER=/tmp/build_tpch_formal_entry_gate_v2.py
CONTROLLER=${BASE}/code/run_tpch_formal_exact_plan_regime_batch.py
CONTAINER=pg5_tpch_ap_slot_node01_20260726
DATABASE=template_fullgrid_tpch_pg5
LOCK=${BASE}/tpch_round49_gate_start_formal_v3.lock
LOG=${BASE}/tpch_round49_gate_start_formal_v3.log

EXPECTED_FORMAL_STATE_SHA=335308f7c19be9bf958b50041f61a8b33fbace17c5ec546942ad35d687f7ecb4
EXPECTED_BASELINE_SHA=50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67
EXPECTED_ANALYZER_SHA=49936cbc4319ef6055d6653c276892cd9df408a98d8560460c3e4f0bd7e37d30
EXPECTED_GATE_BUILDER_SHA=013c6df94cfd1bf7abf6e05bd5c39a66de430e6feb6735a4dd2ed22a41f7dd9b
EXPECTED_CONTROLLER_SHA=5db636e8983b8e743520a870549e744af65e6c40fcb00eab61e66108a091ffb9
EXPECTED_WORKLOAD_SHA=25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008

exec 9>"${LOCK}"
if ! flock -n 9; then
    echo "TPC-H round49 formal gate watcher lock is already held" >&2
    exit 73
fi
exec > >(tee -a "${LOG}") 2>&1

test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = "${EXPECTED_FORMAL_STATE_SHA}"
test "$(sha256sum "${BASELINE}" | awk '{print $1}')" = "${EXPECTED_BASELINE_SHA}"
test "$(sha256sum "${ANALYZER}" | awk '{print $1}')" = "${EXPECTED_ANALYZER_SHA}"
test "$(sha256sum "${GATE_BUILDER}" | awk '{print $1}')" = "${EXPECTED_GATE_BUILDER_SHA}"
test "$(sha256sum "${CONTROLLER}" | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA}"
test "$(tr -d '[:space:]' < "${COUNTER}")" = "1"
test ! -e "${FORMAL_OUTPUT}"

# round49 is produced by the already-running round48 watcher.  Its preparation
# manifest seals the generated Top-5 before this watcher trusts the PID file.
for _ in $(seq 1 720); do
    if test -f "${RUN}/driver.pid" && test -f "${PACKAGE}/top5.json" && test -f "${PREP}/manifest.json"; then
        break
    fi
    sleep 10
done
test -f "${RUN}/driver.pid"
test -f "${PACKAGE}/top5.json"
test -f "${PREP}/manifest.json"
ROUND49_PID="$(tr -d '[:space:]' < "${RUN}/driver.pid")"
case "${ROUND49_PID}" in
    ''|*[!0-9]*) echo "invalid TPC-H round49 PID" >&2; exit 74 ;;
esac
ROUND49_SELECTION_SHA="$(sha256sum "${PACKAGE}/top5.json" | awk '{print $1}')"
test "$(jq -r '.top5_sha256' "${PREP}/manifest.json")" = "${ROUND49_SELECTION_SHA}"
jq -e '
    .diagnostic_only == true and
    .maximum_real_replay_invocations == 5 and
    .qps_used_for_inner_scoring == false and
    [.top5[].selection_role] == ["trusted", "trusted", "trusted", "probe", "probe"] and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    all(
        .top5[];
        .boundary_corner == false and
        .boundary_plateau == false and
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

for _ in $(seq 1 720); do
    if ! kill -0 "${ROUND49_PID}" 2>/dev/null; then
        break
    fi
    ROUND49_COMMAND="$(ps -p "${ROUND49_PID}" -o args=)"
    case "${ROUND49_COMMAND}" in
        *gradient_debug_round49_stability_successor_v2_20260731T1745_package/run_tpch_round29_diagnostic_exact_plan_regime_batch.py*) ;;
        *) echo "TPC-H round49 PID command mismatch" >&2; exit 75 ;;
    esac
    sleep 10
done
if kill -0 "${ROUND49_PID}" 2>/dev/null; then
    echo "TPC-H round49 exceeded formal gate wait limit" >&2
    exit 76
fi

test -f "${RUN}/diagnostic_summary.json"
test -f "${RUN}/diagnostic_state.json"
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
' "${RUN}/diagnostic_state.json" >/dev/null
test "$(sha256sum "${PACKAGE}/top5.json" | awk '{print $1}')" = "${ROUND49_SELECTION_SHA}"
test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = "${EXPECTED_FORMAL_STATE_SHA}"

mkdir -p "${FORMAL_PACKAGE}"
"${PYTHON}" -u "${ANALYZER}" \
    --selection "${PACKAGE}/top5.json" \
    --observations "${RUN}/diagnostic_summary.json" \
    --output "${FORMAL_PACKAGE}/entry_gate_analysis.json"
jq -e '.passed == true' "${FORMAL_PACKAGE}/entry_gate_analysis.json" >/dev/null

# Preserve the exact candidate values and ordering.  Only formal execution
# policy flags change; this is not a new candidate-generation step.
jq --arg source_sha "${ROUND49_SELECTION_SHA}" '
    .diagnostic_only = false |
    .accepted_update_allowed = true |
    .incumbent_update_allowed = true |
    .range_update_allowed = true |
    .gradient_update_allowed = true |
    .formal_expected_improvement_required = false |
    .formal_admission_contract = "diagnostic_quality_gate_passed_then_real_replay_accept_or_reject_v1" |
    .promoted_from_diagnostic_selection_sha256 = $source_sha |
    .top5 |= map(
        .diagnostic_only = false |
        if .selection_role == "trusted" then
            .accepted_update_allowed = true |
            .incumbent_update_allowed = true |
            .range_update_allowed = true |
            .gradient_update_allowed = true
        else
            .accepted_update_allowed = false |
            .incumbent_update_allowed = false |
            .range_update_allowed = false |
            .gradient_update_allowed = false
        end
    )
' "${PACKAGE}/top5.json" > "${FORMAL_PACKAGE}/top5_formal.json.tmp"
mv "${FORMAL_PACKAGE}/top5_formal.json.tmp" "${FORMAL_PACKAGE}/top5_formal.json"

"${PYTHON}" -u "${GATE_BUILDER}" \
    --analysis "${FORMAL_PACKAGE}/entry_gate_analysis.json" \
    --diagnostic-state "${RUN}/diagnostic_state.json" \
    --selection "${FORMAL_PACKAGE}/top5_formal.json" \
    --formal-state "${FORMAL_STATE}" \
    --formal-state-sha256 "${EXPECTED_FORMAL_STATE_SHA}" \
    --output "${FORMAL_PACKAGE}/formal_entry_gate.json"
jq -e '.passed == true' "${FORMAL_PACKAGE}/formal_entry_gate.json" >/dev/null

FORMAL_SELECTION_SHA="$(sha256sum "${FORMAL_PACKAGE}/top5_formal.json" | awk '{print $1}')"
FORMAL_GATE_SHA="$(sha256sum "${FORMAL_PACKAGE}/formal_entry_gate.json" | awk '{print $1}')"
CONFIG_SHA="$(sha256sum "${PACKAGE}/config.json" | awk '{print $1}')"
WORKLOAD=${PACKAGE}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh
test "$(sha256sum "${WORKLOAD}" | awk '{print $1}')" = "${EXPECTED_WORKLOAD_SHA}"

jq -e '
    .diagnostic_only == false and
    .formal_expected_improvement_required == false and
    [.top5[].selection_role] == ["trusted", "trusted", "trusted", "probe", "probe"] and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    ([.top5[] | select(.selection_role == "probe" and .accepted_update_allowed == false and .incumbent_update_allowed == false and .range_update_allowed == false and .gradient_update_allowed == false)] | length) == 2
' "${FORMAL_PACKAGE}/top5_formal.json" >/dev/null
test "$(tr -d '[:space:]' < "${COUNTER}")" = "1"
test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = "${EXPECTED_FORMAL_STATE_SHA}"
test -z "$(ps -eo args= | grep '[r]un_tpch_formal_exact_plan_regime_batch.py' | grep -F "${BASE}" || true)"
test "$(${PYTHON} - <<'PY'
import json
p=json.load(open("/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726/gradient_debug_round49_stability_successor_v2_20260731T1745_package/config.json"))
print((p.get("formal_outer_protocol") or {}).get("formal_search_accepted_count_start"))
PY
)" = "1"

sha256sum \
    "${PACKAGE}/top5.json" \
    "${FORMAL_PACKAGE}/top5_formal.json" \
    "${FORMAL_PACKAGE}/entry_gate_analysis.json" \
    "${FORMAL_PACKAGE}/formal_entry_gate.json" \
    "${CONTROLLER}" \
    "${WORKLOAD}" > "${FORMAL_PACKAGE}/sealed.sha256"

mkdir -p "${FORMAL_OUTPUT}"
nohup env -u LD_LIBRARY_PATH -u PYTHONHOME \
    PYTHONPATH="${PACKAGE}:${VENDOR}:${BASE}/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages" \
    PYTHONNOUSERSITE=1 \
    LD_PRELOAD=/usr/lib64/libstdc++.so.6 \
    PGPASSWORD=postgres PGHOST=127.0.0.1 PGPORT=63301 PGUSER=postgres \
    "${PYTHON}" -u "${CONTROLLER}" \
        --selection "${FORMAL_PACKAGE}/top5_formal.json" \
        --selection-sha256 "${FORMAL_SELECTION_SHA}" \
        --gate-summary "${FORMAL_PACKAGE}/formal_entry_gate.json" \
        --gate-summary-sha256 "${FORMAL_GATE_SHA}" \
        --baseline-summary "${BASELINE}" \
        --baseline-summary-sha256 "${EXPECTED_BASELINE_SHA}" \
        --config "${PACKAGE}/config.json" \
        --config-sha256 "${CONFIG_SHA}" \
        --workload "${WORKLOAD}" \
        --workload-sha256 "${EXPECTED_WORKLOAD_SHA}" \
        --accepted-counter "${COUNTER}" \
        --resume-state "${FORMAL_STATE}" \
        --resume-state-sha256 "${EXPECTED_FORMAL_STATE_SHA}" \
        --output "${FORMAL_OUTPUT}" \
        --container "${CONTAINER}" \
        --database "${DATABASE}" \
        --timeout-seconds 900 > "${FORMAL_OUTPUT}/driver.log" 2>&1 &
FORMAL_PID=$!
printf '%s\n' "${FORMAL_PID}" > "${FORMAL_OUTPUT}/driver.pid"
sleep 5
kill -0 "${FORMAL_PID}"
jq -e --argjson pid "${FORMAL_PID}" '
    .schema_version == "tpch_exact_plan_regime_formal_state_v1" and
    .status == "running" and
    .pid == $pid and
    .accepted_shrink_target == 10 and
    .accepted_count == 1 and
    .physical_call_count == 1 and
    .qps_used_for_inner_scoring == false
' "${FORMAL_OUTPUT}/formal_state.json" >/dev/null
printf 'TPCH_FORMAL_TARGET10_STARTED pid=%s selection_sha=%s\n' "${FORMAL_PID}" "${FORMAL_SELECTION_SHA}"
