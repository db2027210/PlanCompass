#!/usr/bin/env bash
set -euo pipefail

PACKAGE=/tmp/tpcc_formal_batch05_package_v1
TOP5="${PACKAGE}/top5.json"
GATE="${PACKAGE}/continuation_gate.json"
ATTEMPT=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726
STATE="${ATTEMPT}/formal10_tpcc_state.json"
BATCH_ROOT="${ATTEMPT}/formal10_tpcc_batch05_novel_v1_retry1_runnerfix_20260729T2105"
PYTHON=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
FORMAL_RUNNER=/home/anonymous/program/resource_adaptive/tools/run_tpcc_formal_target10_batch.py
DIAGNOSTIC_RUNNER=/home/anonymous/program/resource_adaptive/tools/run_tpcc_round11_calibrated_top5_strict_serial.py
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
