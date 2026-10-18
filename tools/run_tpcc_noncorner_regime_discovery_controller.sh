#!/usr/bin/env bash
set -u

ROOT="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/gradient_debug_noncorner_regime_discovery_v1_20260729T1659"
TOP5="${ROOT}/top5.json"
RUNNER="${ROOT}/run_tpcc_round11_calibrated_top5_strict_serial.py"
BASELINE="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/candidate_measurements/point_20260729_022349_1052396/summary.json"
REPLAY="${ROOT}/top5_real_replay_round12_noncorner_v1"
EXPECTED_TOP5_SHA="2063f33c6c2608b2a81893baed4e87fb40de738fecb31aceee7cf41dfd423d27"
EXPECTED_RUNNER_SHA="0a20dc13455070b94ce9ce30737f80001030dc8e3f9fde16d3c9e57887f1ef76"

if ! test -f "${TOP5}" \
    || ! test -f "${RUNNER}" \
    || ! test -f "${BASELINE}" \
    || test -e "${ROOT}/controller_exit_code.txt" \
    || test -e "${ROOT}/strict_top5_real_replay_round12_ledger_v1.json" \
    || test -e "${REPLAY}/events.jsonl" \
    || test "$(sha256sum "${TOP5}" | awk '{print $1}')" != "${EXPECTED_TOP5_SHA}" \
    || test "$(sha256sum "${RUNNER}" | awk '{print $1}')" != "${EXPECTED_RUNNER_SHA}"; then
    printf 'fail_closed_preflight\n'
    exit 97
fi

PYTHONPATH="/home/anonymous/program/resource_adaptive" \
ADAPTIVE_REAL_REPLAY_ENABLED="0" \
/home/anonymous/miniconda3/envs/resource_adaptive/bin/python \
    "${RUNNER}" \
    "${TOP5}" \
    "${REPLAY}" \
    "${BASELINE}" \
    >"${ROOT}/driver.log" 2>&1
exit_code=$?
printf '%s\n' "${exit_code}" >"${ROOT}/controller_exit_code.txt"
exit "${exit_code}"
