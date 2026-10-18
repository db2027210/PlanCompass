#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
PREP=${BASE}/gradient_debug_round47_stability_recovery_prep_v2_20260731T1655
SOURCE=${BASE}/gradient_debug_round45_topology_v2_corrected_validation_v3_20260731T1550_package
PACKAGE=${BASE}/gradient_debug_round47_stability_probe5_v2_20260731T1705_package
OUTPUT=${BASE}/gradient_debug_round47_stability_probe5_v2_20260731T1705_run
FORMAL=${BASE}/formal10_exact_plan_regime_round28_feedback_v1_20260729T2055_run/formal_state.json
BASELINE=${BASE}/candidate_measurements/formal_point_20260729T050311Z_3000/summary.json
COUNTER=${BASE}/run/attempt48_resource_correction_22c1730m16r20w36w_rolling_v2.accepted_shrink.counter.txt
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
VENDOR=${BASE}/vendor/py311
CONTAINER=pg5_tpch_ap_slot_node01_20260726
DATABASE=template_fullgrid_tpch_pg5

test ! -e "${PACKAGE}"
test ! -e "${OUTPUT}"
test "$(sha256sum "${PREP}/top5.json" | awk '{print $1}')" = \
    fa1d1ab349714ae684b95a08b4348a88200e549f93a414bc3acdaaf523d98d34
test "$(sha256sum "${PREP}/manifest.json" | awk '{print $1}')" = \
    39d5dc796e1a084222647e7990b76f5e18bf880e3b8d379f98bb3f62fdb4ed26
test "$(sha256sum "${FORMAL}" | awk '{print $1}')" = \
    335308f7c19be9bf958b50041f61a8b33fbace17c5ec546942ad35d687f7ecb4
test "$(sha256sum "${SOURCE}/config.json" | awk '{print $1}')" = \
    0b6be9e78681028b730682e87bf01a76306169336e921c60b03bd7c0dfd69b92
test "$(sha256sum "${SOURCE}/gate.json" | awk '{print $1}')" = \
    02d19baf6ad8ef9bce7432243ed1ffcb97ed7b402e2fe7c4199243260c109be1
test "$(sha256sum "${SOURCE}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh" | awk '{print $1}')" = \
    25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008
test "$(sha256sum "${SOURCE}/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py" | awk '{print $1}')" = \
    31c0080ec1487d0f4cb7a840c2584d890c5e780f561f6c4a480099ba029d80eb
test "$(sha256sum "${BASELINE}" | awk '{print $1}')" = \
    50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67
test "$(sha256sum /tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v3.py | awk '{print $1}')" = \
    7bc1528c45de80196aaefc153a0d36e0f0e4b86fb544bbf30578cfc930c100dd
test "$(sha256sum /tmp/validate_tpch_round29_diagnostic_package_v3.py | awk '{print $1}')" = \
    34197a0ef8cb0b6ddcea5f5413b6a1e3a72cc13f34d830a05922b7eb089abf06
test "$(sha256sum /tmp/tpch_round19_plan_regime_select.py | awk '{print $1}')" = \
    68820feeed713f059925862e39709b046f15c510f01b2709b86ac6a57ccd88ab
test "$(tr -d '[:space:]' < "${COUNTER}")" = "1"

mkdir -p "${PACKAGE}"
cp "${PREP}/top5.json" "${PACKAGE}/top5.json"
cp "${PREP}/manifest.json" "${PACKAGE}/preparation_manifest.json"
cp "${FORMAL}" "${PACKAGE}/resume_state.json"
cp "${SOURCE}/config.json" "${PACKAGE}/config.json"
cp "${SOURCE}/gate.json" "${PACKAGE}/gate.json"
cp "${SOURCE}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh" \
    "${PACKAGE}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh"
cp "${SOURCE}/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py" \
    "${PACKAGE}/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py"
cp "${SOURCE}/formal_measurement.py" "${PACKAGE}/formal_measurement.py"
cp "${SOURCE}/formal_contracts.py" "${PACKAGE}/formal_contracts.py"
cp "${SOURCE}/system_resource_monitor.py" "${PACKAGE}/system_resource_monitor.py"
cp /tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v3.py \
    "${PACKAGE}/run_tpch_round29_diagnostic_exact_plan_regime_batch.py"
cp /tmp/validate_tpch_round29_diagnostic_package_v3.py \
    "${PACKAGE}/validate_tpch_round29_diagnostic_package.py"
cp /tmp/tpch_round19_plan_regime_select.py \
    "${PACKAGE}/tpch_round19_plan_regime_select.py"

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
        --expected-workload-driver-sha256 \
            31c0080ec1487d0f4cb7a840c2584d890c5e780f561f6c4a480099ba029d80eb \
        >"${PACKAGE}/preflight_validation.json"
jq -e '
    .status == "passed" and
    .roles == ["probe", "probe", "probe", "probe", "probe"] and
    .unique_fingerprint_count == 5 and
    .unique_parameter_count == 5 and
    .maximum_physical_calls == 5
' "${PACKAGE}/preflight_validation.json" >/dev/null

test -z "$(
    ps -eo comm,args |
        awk '$1 ~ /python/ && $0 ~ /run_tpch_round29_diagnostic_exact_plan_regime_batch/ {print}'
)"
test "$(
    docker inspect -f \
        '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.CpusetCpus}}' \
        "${CONTAINER}"
)" = "true 22000000000 1730000000 1730000000 0-15,36-51"

SELECTION_SHA="$(sha256sum "${PACKAGE}/top5.json" | awk '{print $1}')"
RUNNER_SHA="$(sha256sum "${PACKAGE}/run_tpch_round29_diagnostic_exact_plan_regime_batch.py" | awk '{print $1}')"
CONFIG_SHA="$(sha256sum "${PACKAGE}/config.json" | awk '{print $1}')"
GATE_SHA="$(sha256sum "${PACKAGE}/gate.json" | awk '{print $1}')"
WORKLOAD_SHA="$(sha256sum "${PACKAGE}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh" | awk '{print $1}')"
RESUME_SHA="$(sha256sum "${PACKAGE}/resume_state.json" | awk '{print $1}')"

jq -n \
    --arg selection_sha "${SELECTION_SHA}" \
    --arg runner_sha "${RUNNER_SHA}" \
    --arg config_sha "${CONFIG_SHA}" \
    --arg gate_sha "${GATE_SHA}" \
    --arg workload_sha "${WORKLOAD_SHA}" \
    --arg resume_sha "${RESUME_SHA}" \
    '{
        schema_version: "tpch_stability_probe5_package_manifest_v1",
        status: "ready",
        selection_sha256: $selection_sha,
        runner_sha256: $runner_sha,
        config_sha256: $config_sha,
        gate_sha256: $gate_sha,
        workload_sha256: $workload_sha,
        resume_state_sha256: $resume_sha,
        maximum_physical_calls: 5,
        diagnostic_only: true,
        deterministic_preparation: true
    }' >"${PACKAGE}/package_manifest.json"

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
        --baseline-summary-sha256 \
            50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67 \
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
PID=$!
printf '%s\n' "${PID}" >"${OUTPUT}/driver.pid"
sleep 3
kill -0 "${PID}"
printf 'PID=%s\n' "${PID}"
