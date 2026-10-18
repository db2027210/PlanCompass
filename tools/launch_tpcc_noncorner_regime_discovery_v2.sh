#!/usr/bin/env bash
set -euo pipefail

ROOT="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/gradient_debug_noncorner_regime_discovery_v2_20260729T1702"
SOURCE="/tmp/tpcc_noncorner_regime_discovery_top5.json"
RUNNER="/home/anonymous/program/resource_adaptive/tools/run_tpcc_round11_calibrated_top5_strict_serial.py"
CONTROLLER="/home/anonymous/program/resource_adaptive/tools/run_tpcc_noncorner_regime_discovery_v2_controller.sh"
DEPENDENCY_ROOT="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/gradient_debug_round13_exact_plan_regime_corrected_v1_20260729"
EXPECTED_SOURCE_SHA="2063f33c6c2608b2a81893baed4e87fb40de738fecb31aceee7cf41dfd423d27"
EXPECTED_RUNNER_SHA="0a20dc13455070b94ce9ce30737f80001030dc8e3f9fde16d3c9e57887f1ef76"
EXPECTED_ADAPTIVE_SHA="c3c7a826ff1ab65c04c93510efd17b26907a79a15b3d13ff91e5ca106de94584"
EXPECTED_CORRECTNESS_SHA="e6bb7aefc67eb01e3d9138c7bf36cf93367d87779d58013cad41b2302f9c05f7"
EXPECTED_SELECTOR_SHA="8c963c9f730e1038b61146e2e928b946a6b213e7ee95b9aa6b9c4ebdf0608376"

test ! -e "${ROOT}"
test "$(sha256sum "${SOURCE}" | awk '{print $1}')" = "${EXPECTED_SOURCE_SHA}"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${DEPENDENCY_ROOT}/adaptive_replay_policy.py" | awk '{print $1}')" = "${EXPECTED_ADAPTIVE_SHA}"
test "$(sha256sum "${DEPENDENCY_ROOT}/replay_correctness_policy.py" | awk '{print $1}')" = "${EXPECTED_CORRECTNESS_SHA}"
test "$(sha256sum "${DEPENDENCY_ROOT}/tpcc_remaining_budget_selector.py" | awk '{print $1}')" = "${EXPECTED_SELECTOR_SHA}"

mkdir -p "${ROOT}"
cp "${SOURCE}" "${ROOT}/top5.json"
cp "${RUNNER}" "${ROOT}/run_tpcc_round11_calibrated_top5_strict_serial.py"
nohup "${CONTROLLER}" >"${ROOT}/controller.log" 2>&1 &
controller_pid=$!
printf '%s\n' "${controller_pid}" >"${ROOT}/controller.pid"

printf 'ROOT=%s\n' "${ROOT}"
printf 'PID=%s\n' "${controller_pid}"
