#!/usr/bin/env bash
set -euo pipefail

PACKAGE=/tmp/tpcc_formal_batch05_package_v1
RUNNER_PACKAGE=/tmp/tpcc_batch05_runnerfix_v2
TOP5="${PACKAGE}/top5.json"
GATE="${PACKAGE}/continuation_gate.json"
ATTEMPT=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726
STATE="${ATTEMPT}/formal10_tpcc_state.json"
BATCH_ROOT="${ATTEMPT}/formal10_tpcc_batch05_novel_v1_retry2_runnerdeps_20260729T2110"
PYTHON=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
FORMAL_RUNNER=/home/anonymous/program/resource_adaptive/tools/run_tpcc_formal_target10_batch.py
DIAGNOSTIC_RUNNER="${RUNNER_PACKAGE}/run_tpcc_round11_calibrated_top5_strict_serial.py"
BASELINE="${ATTEMPT}/candidate_measurements/point_20260729_022349_1052396/summary.json"

test "$(sha256sum "$TOP5" | awk '{print $1}')" = \
    "f39d2b4d404897a1f976c9f457a1fbb06c807d7f0eeee5e00040262ade860b6a"
test "$(sha256sum "$GATE" | awk '{print $1}')" = \
    "37e6d60f878f81624640260d7760eddd2afb6c573cfcc7a77a81f95137696e07"
test "$(sha256sum "$STATE" | awk '{print $1}')" = \
    "c802eb584841a098de2ccee7bc2cea8bdf23d3fb7440081c85aeb5f5e958b13c"
test "$(sha256sum "$FORMAL_RUNNER" | awk '{print $1}')" = \
    "b3490570631ff7b6f36110a56472abd5816119c3fb44c68503b47291d487b627"
test "$(sha256sum "$DIAGNOSTIC_RUNNER" | awk '{print $1}')" = \
    "df1f9f9b43656214cdccace0e3614fada7d2efc7c7679942da6478ac0f042d68"
test "$(sha256sum "${RUNNER_PACKAGE}/adaptive_replay_policy.py" | awk '{print $1}')" = \
    "c3c7a826ff1ab65c04c93510efd17b26907a79a15b3d13ff91e5ca106de94584"
test "$(sha256sum "${RUNNER_PACKAGE}/tpcc_round11_current_correctness_policy.py" | awk '{print $1}')" = \
    "e6bb7aefc67eb01e3d9138c7bf36cf93367d87779d58013cad41b2302f9c05f7"
test "$(sha256sum "${RUNNER_PACKAGE}/tpcc_remaining_budget_selector.py" | awk '{print $1}')" = \
    "8c963c9f730e1038b61146e2e928b946a6b213e7ee95b9aa6b9c4ebdf0608376"
test "$(sha256sum "${RUNNER_PACKAGE}/extract_tpcc_round11_calibrated_top5.py" | awk '{print $1}')" = \
    "42714617c1f965e19b5cfb13caa45cafb0274479806eb5a5963344ab361e0adb"
test "$(sha256sum "$BASELINE" | awk '{print $1}')" = \
    "9c0f0d083796bef1ec8d49c4aa71b1d8e904fca43dbfb49a1708c0c40ccd7356"
test ! -e "$BATCH_ROOT"
if pgrep -af '[r]un_tpcc_round11_calibrated_top5_strict_serial.py' \
    >/dev/null
then
    echo "TPC-C diagnostic runner already active" >&2
    exit 73
fi

exec "$PYTHON" -u "$FORMAL_RUNNER" \
    "$TOP5" \
    "f39d2b4d404897a1f976c9f457a1fbb06c807d7f0eeee5e00040262ade860b6a" \
    "$GATE" \
    "37e6d60f878f81624640260d7760eddd2afb6c573cfcc7a77a81f95137696e07" \
    "$DIAGNOSTIC_RUNNER" "$BASELINE" "$STATE" "$BATCH_ROOT"
