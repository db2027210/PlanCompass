#!/usr/bin/env bash
set -u

ROOT="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/gradient_debug_noncorner_regime_discovery_v2_20260729T1702"
DEPENDENCY_ROOT="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/gradient_debug_round13_exact_plan_regime_corrected_v1_20260729"
TOP5="${ROOT}/top5.json"
RUNNER="${ROOT}/run_tpcc_round11_calibrated_top5_strict_serial.py"
BASELINE="/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/candidate_measurements/point_20260729_022349_1052396/summary.json"
REPLAY="${ROOT}/top5_real_replay_round12_noncorner_v2"

PYTHONPATH="${DEPENDENCY_ROOT}:/home/anonymous/program/resource_adaptive/tools:/home/anonymous/program/resource_adaptive" \
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
