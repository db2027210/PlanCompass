#!/usr/bin/env bash
set -euo pipefail

ROOT="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/gradient_debug_round14_robust_regime_interval_v2_20260729T1824"
SOURCE="/tmp/tpcc_round14_robust_regime_top5_v3.json"
RUNNER="/home/anonymous/program/resource_adaptive/tools/run_tpcc_round11_calibrated_top5_strict_serial.py"
CONTROLLER="/home/anonymous/program/resource_adaptive/tools/run_tpcc_round14_robust_controller.sh"
DEPENDENCY_ROOT="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/gradient_debug_round13_exact_plan_regime_corrected_v1_20260729"
EXPECTED_SOURCE_SHA="a973acb0608118be851d21625dc781b1ff4bd4e0cdb26ea8b68e44fb2f798d8b"
EXPECTED_RUNNER_SHA="df1f9f9b43656214cdccace0e3614fada7d2efc7c7679942da6478ac0f042d68"
EXPECTED_ADAPTIVE_SHA="c3c7a826ff1ab65c04c93510efd17b26907a79a15b3d13ff91e5ca106de94584"
EXPECTED_CORRECTNESS_SHA="e6bb7aefc67eb01e3d9138c7bf36cf93367d87779d58013cad41b2302f9c05f7"
EXPECTED_SELECTOR_SHA="8c963c9f730e1038b61146e2e928b946a6b213e7ee95b9aa6b9c4ebdf0608376"

test ! -e "${ROOT}"
test "$(sha256sum "${SOURCE}" | awk '{print $1}')" = "${EXPECTED_SOURCE_SHA}"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${DEPENDENCY_ROOT}/adaptive_replay_policy.py" | awk '{print $1}')" = "${EXPECTED_ADAPTIVE_SHA}"
test "$(sha256sum "${DEPENDENCY_ROOT}/replay_correctness_policy.py" | awk '{print $1}')" = "${EXPECTED_CORRECTNESS_SHA}"
test "$(sha256sum "${DEPENDENCY_ROOT}/tpcc_remaining_budget_selector.py" | awk '{print $1}')" = "${EXPECTED_SELECTOR_SHA}"
test "$(jq -r '.maximum_real_workload_invocations' "${SOURCE}")" = "5"
test "$(jq -c '[.top5[].selection_role]' "${SOURCE}")" = '["trusted","trusted","trusted","uncertain_probe","uncertain_probe"]'
test "$(jq -r '[.top5[].three_level_fingerprint.id] | unique | length' "${SOURCE}")" = "5"
test "$(jq -c '.state_mutation_contract' "${SOURCE}")" = '{"accepted_update":false,"gradient_update":false,"incumbent_update":false,"range_update":false}'

mkdir -p "${ROOT}"
cp "${SOURCE}" "${ROOT}/top5.json"
cp "${RUNNER}" "${ROOT}/run_tpcc_round11_calibrated_top5_strict_serial.py"
cp "${DEPENDENCY_ROOT}/adaptive_replay_policy.py" "${ROOT}/adaptive_replay_policy.py"
cp "${DEPENDENCY_ROOT}/replay_correctness_policy.py" "${ROOT}/replay_correctness_policy.py"
cp "${DEPENDENCY_ROOT}/tpcc_remaining_budget_selector.py" "${ROOT}/tpcc_remaining_budget_selector.py"
nohup "${CONTROLLER}" >"${ROOT}/controller.log" 2>&1 &
controller_pid=$!
printf '%s\n' "${controller_pid}" >"${ROOT}/controller.pid"
sleep 5
kill -0 "${controller_pid}"
printf 'ROOT=%s\n' "${ROOT}"
printf 'PID=%s\n' "${controller_pid}"
