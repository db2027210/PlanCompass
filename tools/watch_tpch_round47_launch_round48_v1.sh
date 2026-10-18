#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
CURRENT_PACKAGE=${BASE}/gradient_debug_round47_stability_probe5_v2_20260731T1705_package
CURRENT_RUN=${BASE}/gradient_debug_round47_stability_probe5_v2_20260731T1705_run
PREP=${BASE}/gradient_debug_round48_stability_successor_prep_v1_20260731T1720
PACKAGE=${BASE}/gradient_debug_round48_stability_successor_v1_20260731T1720_package
OUTPUT=${BASE}/gradient_debug_round48_stability_successor_v1_20260731T1720_run
FORMAL=${BASE}/formal10_exact_plan_regime_round28_feedback_v1_20260729T2055_run/formal_state.json
BASELINE=${BASE}/candidate_measurements/formal_point_20260729T050311Z_3000/summary.json
COUNTER=${BASE}/run/attempt48_resource_correction_22c1730m16r20w36w_rolling_v2.accepted_shrink.counter.txt
HISTORY=${BASE}/gradient_debug_round39_latest_epoch_exact_v2_20260731T0725_package/expanded_analysis.json
CONFIG=${BASE}/gradient_debug_round45_topology_v2_corrected_validation_v3_20260731T1550_package/config.json
CAPTURE=${BASE}/gradient_debug_round37_corrected3p2_diagnosticfix_v1_20260731T0525/query_capture_exact.json
OLDER_SELECTION=${BASE}/gradient_debug_round45_topology_v2_corrected_validation_v3_20260731T1550_package/top5.json
SOURCE_PACKAGE=${BASE}/gradient_debug_round45_topology_v2_corrected_validation_v3_20260731T1550_package
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
VENDOR=${BASE}/vendor/py311
EXPANDER=/tmp/tpch_exact_regime_neighbor_expand_v2.py
BUILDER=/tmp/build_tpch_autonomous_successor_top5_v2.py
SELECTOR=/tmp/tpch_round19_plan_regime_select.py
RUNNER=/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v3.py
VALIDATOR=/tmp/validate_tpch_round29_diagnostic_package_v3.py
CURRENT_PID=21698
CONTAINER=pg5_tpch_ap_slot_node01_20260726
DATABASE=template_fullgrid_tpch_pg5
LOCK=${CURRENT_RUN}.round48_watcher.lock
LOG=${CURRENT_RUN}/round48_watcher.log

EXPECTED_CURRENT_SELECTION_SHA=fa1d1ab349714ae684b95a08b4348a88200e549f93a414bc3acdaaf523d98d34
EXPECTED_FORMAL_SHA=335308f7c19be9bf958b50041f61a8b33fbace17c5ec546942ad35d687f7ecb4
EXPECTED_CONFIG_SHA=0b6be9e78681028b730682e87bf01a76306169336e921c60b03bd7c0dfd69b92
EXPECTED_CAPTURE_SHA=8021855b122cfc27c3f709abbdaa620d72c69b6ac77238d26abb8a4d575ad66d
EXPECTED_EXPANDER_SHA=a4fb3fd997cc92d7c7c217086627d30c72caf3d2a7f74347ad28be7b0ca54857
EXPECTED_BUILDER_SHA=28b5d008133db7963138a7dafccac15612ed53b7f232c34e920978dc3440e49f
EXPECTED_SELECTOR_SHA=68820feeed713f059925862e39709b046f15c510f01b2709b86ac6a57ccd88ab
EXPECTED_RUNNER_SHA=7bc1528c45de80196aaefc153a0d36e0f0e4b86fb544bbf30578cfc930c100dd
EXPECTED_VALIDATOR_SHA=34197a0ef8cb0b6ddcea5f5413b6a1e3a72cc13f34d830a05922b7eb089abf06
EXPECTED_WORKLOAD_SHA=25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008
EXPECTED_DRIVER_SHA=31c0080ec1487d0f4cb7a840c2584d890c5e780f561f6c4a480099ba029d80eb
EXPECTED_BASELINE_SHA=50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67

exec 9>"${LOCK}"
if ! flock -n 9; then
    echo "TPC-H round48 watcher lock is already held" >&2
    exit 73
fi
exec > >(tee -a "${LOG}") 2>&1

test "$(sha256sum "${CURRENT_PACKAGE}/top5.json" | awk '{print $1}')" = \
    "${EXPECTED_CURRENT_SELECTION_SHA}"
test "$(sha256sum "${FORMAL}" | awk '{print $1}')" = "${EXPECTED_FORMAL_SHA}"
test "$(sha256sum "${CONFIG}" | awk '{print $1}')" = "${EXPECTED_CONFIG_SHA}"
test "$(sha256sum "${CAPTURE}" | awk '{print $1}')" = "${EXPECTED_CAPTURE_SHA}"
test "$(sha256sum "${EXPANDER}" | awk '{print $1}')" = "${EXPECTED_EXPANDER_SHA}"
test "$(sha256sum "${BUILDER}" | awk '{print $1}')" = "${EXPECTED_BUILDER_SHA}"
test "$(sha256sum "${SELECTOR}" | awk '{print $1}')" = "${EXPECTED_SELECTOR_SHA}"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${VALIDATOR}" | awk '{print $1}')" = "${EXPECTED_VALIDATOR_SHA}"
test "$(sha256sum "${BASELINE}" | awk '{print $1}')" = "${EXPECTED_BASELINE_SHA}"

for _ in $(seq 1 720); do
    if ! kill -0 "${CURRENT_PID}" 2>/dev/null; then
        break
    fi
    CURRENT_COMMAND="$(ps -p "${CURRENT_PID}" -o args=)"
    case "${CURRENT_COMMAND}" in
        *gradient_debug_round47_stability_probe5_v2_20260731T1705_package/run_tpch_round29_diagnostic_exact_plan_regime_batch.py*)
            ;;
        *)
            echo "TPC-H round47 PID command mismatch" >&2
            exit 74
            ;;
    esac
    sleep 10
done
if kill -0 "${CURRENT_PID}" 2>/dev/null; then
    echo "TPC-H round47 exceeded watcher wait limit" >&2
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
    .accepted_update_allowed == false and
    .incumbent_update_allowed == false and
    .range_update_allowed == false and
    .gradient_update_allowed == false
' "${CURRENT_RUN}/diagnostic_state.json" >/dev/null
test "$(sha256sum "${FORMAL}" | awk '{print $1}')" = "${EXPECTED_FORMAL_SHA}"

test ! -e "${PREP}"
test ! -e "${PACKAGE}"
test ! -e "${OUTPUT}"
mkdir -p "${PREP}"
CURRENT_SUMMARY_SHA="$(sha256sum "${CURRENT_RUN}/diagnostic_summary.json" | awk '{print $1}')"

env -u LD_LIBRARY_PATH -u PYTHONHOME \
    PYTHONPATH="${VENDOR}:${BASE}/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages" \
    PYTHONNOUSERSITE=1 \
    LD_PRELOAD=/usr/lib64/libstdc++.so.6 \
    PGPASSWORD=postgres \
    timeout 1800s "${PYTHON}" -u "${EXPANDER}" \
        --history "${HISTORY}" \
        --evidence-top5 "${CURRENT_PACKAGE}/top5.json" \
        --config "${CONFIG}" \
        --capture "${CAPTURE}" \
        --selector "${SELECTOR}" \
        --code-root "${BASE}/code" \
        --allow-live-epoch-transition \
        --output "${PREP}/expanded_candidates.json"

for suffix in a b; do
    "${PYTHON}" -u "${BUILDER}" \
        --previous-selection "${CURRENT_PACKAGE}/top5.json" \
        --previous-summary "${CURRENT_RUN}/diagnostic_summary.json" \
        --expanded-candidates "${PREP}/expanded_candidates.json" \
        --additional-exclusion-selection "${OLDER_SELECTION}" \
        --output "${PREP}/top5_${suffix}.json"
done
cmp -s "${PREP}/top5_a.json" "${PREP}/top5_b.json"
cp "${PREP}/top5_a.json" "${PREP}/top5.json"
test "$(sha256sum "${CURRENT_RUN}/diagnostic_summary.json" | awk '{print $1}')" = \
    "${CURRENT_SUMMARY_SHA}"

jq -e '
    .diagnostic_only == true and
    .maximum_real_replay_invocations == 5 and
    .qps_used_for_inner_scoring == false and
    (
        [.top5[].selection_role] ==
            ["probe", "probe", "probe", "probe", "probe"] or
        [.top5[].selection_role] ==
            ["trusted", "trusted", "trusted", "probe", "probe"]
    ) and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    ([.top5[].candidate_params] | unique | length) == 5 and
    all(
        .top5[];
        .boundary_corner == false and
        .boundary_plateau == false and
        .actual_parameter_noncorner_audit.reason ==
            "actual_parameter_space_noncorner" and
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
' "${PREP}/top5.json" >/dev/null
test "$(sha256sum "${FORMAL}" | awk '{print $1}')" = "${EXPECTED_FORMAL_SHA}"

jq -n \
    --arg current_summary_sha "${CURRENT_SUMMARY_SHA}" \
    --arg expanded_sha "$(sha256sum "${PREP}/expanded_candidates.json" | awk '{print $1}')" \
    --arg top5_sha "$(sha256sum "${PREP}/top5.json" | awk '{print $1}')" \
    --arg formal_state_sha "$(sha256sum "${FORMAL}" | awk '{print $1}')" \
    '{
        schema_version: "tpch_stability_successor_prep_manifest_v1",
        status: "ready",
        workload_calls: 0,
        deterministic_top5_rebuild_match: true,
        source_summary_sha256: $current_summary_sha,
        expanded_candidates_sha256: $expanded_sha,
        top5_sha256: $top5_sha,
        formal_state_sha256: $formal_state_sha
    }' >"${PREP}/manifest.json"

mkdir -p "${PACKAGE}"
cp "${PREP}/top5.json" "${PACKAGE}/top5.json"
cp "${PREP}/manifest.json" "${PACKAGE}/preparation_manifest.json"
cp "${FORMAL}" "${PACKAGE}/resume_state.json"
cp "${CONFIG}" "${PACKAGE}/config.json"
cp "${SOURCE_PACKAGE}/gate.json" "${PACKAGE}/gate.json"
cp "${SOURCE_PACKAGE}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh" \
    "${PACKAGE}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh"
cp "${SOURCE_PACKAGE}/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py" \
    "${PACKAGE}/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py"
cp "${SOURCE_PACKAGE}/formal_measurement.py" "${PACKAGE}/formal_measurement.py"
cp "${SOURCE_PACKAGE}/formal_contracts.py" "${PACKAGE}/formal_contracts.py"
cp "${SOURCE_PACKAGE}/system_resource_monitor.py" "${PACKAGE}/system_resource_monitor.py"
cp "${RUNNER}" "${PACKAGE}/run_tpch_round29_diagnostic_exact_plan_regime_batch.py"
cp "${VALIDATOR}" "${PACKAGE}/validate_tpch_round29_diagnostic_package.py"
cp "${SELECTOR}" "${PACKAGE}/tpch_round19_plan_regime_select.py"

SELECTION_SHA="$(sha256sum "${PACKAGE}/top5.json" | awk '{print $1}')"
CONFIG_SHA="$(sha256sum "${PACKAGE}/config.json" | awk '{print $1}')"
GATE_SHA="$(sha256sum "${PACKAGE}/gate.json" | awk '{print $1}')"
WORKLOAD_SHA="$(sha256sum "${PACKAGE}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh" | awk '{print $1}')"
DRIVER_SHA="$(sha256sum "${PACKAGE}/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py" | awk '{print $1}')"
RESUME_SHA="$(sha256sum "${PACKAGE}/resume_state.json" | awk '{print $1}')"
test "${WORKLOAD_SHA}" = "${EXPECTED_WORKLOAD_SHA}"
test "${DRIVER_SHA}" = "${EXPECTED_DRIVER_SHA}"

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
        --workload "${PACKAGE}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh" \
        --workload-driver "${PACKAGE}/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py" \
        --expected-workload-driver-sha256 "${EXPECTED_DRIVER_SHA}" \
        >"${PACKAGE}/preflight_validation.json"
jq -e '
    .status == "passed" and
    (
        .roles == ["probe", "probe", "probe", "probe", "probe"] or
        .roles == ["trusted", "trusted", "trusted", "probe", "probe"]
    ) and
    .unique_fingerprint_count == 5 and
    .unique_parameter_count == 5 and
    .maximum_physical_calls == 5
' "${PACKAGE}/preflight_validation.json" >/dev/null

test "$(
    docker inspect -f \
        '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.CpusetCpus}}' \
        "${CONTAINER}"
)" = "true 22000000000 1730000000 1730000000 0-15,36-51"
test "$(tr -d '[:space:]' < "${COUNTER}")" = "1"
test "$(sha256sum "${FORMAL}" | awk '{print $1}')" = "${EXPECTED_FORMAL_SHA}"

mkdir -p "${OUTPUT}"
nohup env -u LD_LIBRARY_PATH -u PYTHONHOME \
    PYTHONPATH="${PACKAGE}:${VENDOR}:${BASE}/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages" \
    PYTHONNOUSERSITE=1 \
    LD_PRELOAD=/usr/lib64/libstdc++.so.6 \
    PGPASSWORD=postgres PGHOST=127.0.0.1 PGPORT=63301 PGUSER=postgres \
    "${PYTHON}" -u \
        "${PACKAGE}/run_tpch_round29_diagnostic_exact_plan_regime_batch.py" \
        --diagnostic-only \
        --selection "${PACKAGE}/top5.json" \
        --selection-sha256 "${SELECTION_SHA}" \
        --gate-summary "${PACKAGE}/gate.json" \
        --gate-summary-sha256 "${GATE_SHA}" \
        --baseline-summary "${BASELINE}" \
        --baseline-summary-sha256 "${EXPECTED_BASELINE_SHA}" \
        --config "${PACKAGE}/config.json" \
        --config-sha256 "${CONFIG_SHA}" \
        --workload "${PACKAGE}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh" \
        --workload-sha256 "${WORKLOAD_SHA}" \
        --accepted-counter "${COUNTER}" \
        --resume-state "${PACKAGE}/resume_state.json" \
        --resume-state-sha256 "${RESUME_SHA}" \
        --selection-start-rank 1 \
        --maximum-real-replay-invocations 5 \
        --output "${OUTPUT}" \
        --container "${CONTAINER}" \
        --database "${DATABASE}" \
        --timeout-seconds 900 >"${OUTPUT}/driver.log" 2>&1 &
NEXT_PID=$!
printf '%s\n' "${NEXT_PID}" >"${OUTPUT}/driver.pid"
sleep 3
kill -0 "${NEXT_PID}"
printf 'round48_pid=%s selection_sha=%s\n' "${NEXT_PID}" "${SELECTION_SHA}"
