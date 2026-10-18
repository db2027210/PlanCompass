#!/usr/bin/env bash
set -euo pipefail

ROOT="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/gradient_debug_noncorner_regime_discovery_v1_20260729T1659"
SOURCE="/tmp/tpcc_noncorner_regime_discovery_top5.json"
RUNNER="/home/anonymous/program/resource_adaptive/tools/run_tpcc_round11_calibrated_top5_strict_serial.py"
BASELINE="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/candidate_measurements/point_20260729_022349_1052396/summary.json"
REPLAY="${ROOT}/top5_real_replay_round12_noncorner_v1"
EXPECTED_SOURCE_SHA="2063f33c6c2608b2a81893baed4e87fb40de738fecb31aceee7cf41dfd423d27"
EXPECTED_RUNNER_SHA="0a20dc13455070b94ce9ce30737f80001030dc8e3f9fde16d3c9e57887f1ef76"

test ! -e "${ROOT}"
test -f "${SOURCE}"
test -f "${RUNNER}"
test -f "${BASELINE}"
test "$(sha256sum "${SOURCE}" | awk '{print $1}')" = "${EXPECTED_SOURCE_SHA}"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"

mkdir -p "${ROOT}"
cp "${SOURCE}" "${ROOT}/top5.json"
cp "${RUNNER}" "${ROOT}/run_tpcc_round11_calibrated_top5_strict_serial.py"
test "$(sha256sum "${ROOT}/top5.json" | awk '{print $1}')" = "${EXPECTED_SOURCE_SHA}"
test "$(sha256sum "${ROOT}/run_tpcc_round11_calibrated_top5_strict_serial.py" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"

(
    set +e
    PYTHONPATH="/home/anonymous/program/resource_adaptive" \
    ADAPTIVE_REAL_REPLAY_ENABLED="0" \
    /home/anonymous/miniconda3/envs/resource_adaptive/bin/python \
        "${ROOT}/run_tpcc_round11_calibrated_top5_strict_serial.py" \
        "${ROOT}/top5.json" \
        "${REPLAY}" \
        "${BASELINE}" \
        >"${ROOT}/driver.log" 2>&1
    exit_code=$?
    printf '%s\n' "${exit_code}" >"${ROOT}/controller_exit_code.txt"
    exit "${exit_code}"
) >"${ROOT}/controller.log" 2>&1 &
controller_pid=$!
printf '%s\n' "${controller_pid}" >"${ROOT}/controller.pid"

printf 'ROOT=%s\n' "${ROOT}"
printf 'PID=%s\n' "${controller_pid}"
printf 'TOP5_SHA=%s\n' "${EXPECTED_SOURCE_SHA}"
printf 'RUNNER_SHA=%s\n' "${EXPECTED_RUNNER_SHA}"
