#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
B=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726
PACKAGE=${PROJECT}/history/tpcc_final_algorithm_fresh10_v27_20260731/entry_validation
SELECTION=${PACKAGE}/top5_a.json
RUN=${B}/autonomous_target10_controller_v4/runs/autonomous_diagnostic_validation_51_score_span_repeat_v27
REPLAY=${RUN}/top5_real_replay_round14_diagnostic_v1
LEDGER=${RUN}/strict_top5_real_replay_round14_ledger_v1.json
SUMMARY=${REPLAY}/summary.json
FORMAL_STATE=${B}/formal10_tpcc_state.json
BASELINE=${B}/candidate_measurements/point_20260729_022349_1052396/summary.json
RUNNER=${B}/gradient_debug_round14_robust_regime_interval_v2_20260729T1824/run_tpcc_round11_calibrated_top5_strict_serial.py
CONTROLLER=/tmp/tpcc_autonomous_target10_package_v23_20260731/tpcc_autonomous_target10_controller_v23.py

test ! -e "${RUN}"
test "$(sha256sum "${SELECTION}" | awk '{print $1}')" = \
    bd1b828cf1ca48e31680ae8f51a66d8c69cc60d78f6be169353d528ea59c5139
test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = \
    f8f16a0c8ed261e2158fa5c54f061ebb4ce3aa45e6eab816c9db40bf40ab588a
test "$(sha256sum "${BASELINE}" | awk '{print $1}')" = \
    9c0f0d083796bef1ec8d49c4aa71b1d8e904fca43dbfb49a1708c0c40ccd7356
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = \
    df1f9f9b43656214cdccace0e3614fada7d2efc7c7679942da6478ac0f042d68

/home/anonymous/miniconda3/envs/resource_adaptive/bin/python - \
    "${CONTROLLER}" "${SELECTION}" <<'PY'
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("tpcc_controller_v23", Path(sys.argv[1]))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.validate_selection(module.load_json(Path(sys.argv[2])), mode="diagnostic_validation")
print("TPCC_REPEAT51_PREFLIGHT_OK", flush=True)
PY

mkdir -p "${RUN}"
cp "${SELECTION}" "${RUN}/top5.json"
PYTHONPATH=${PROJECT}:${B}/gradient_debug_round14_robust_regime_interval_v2_20260729T1824 \
    /home/anonymous/miniconda3/envs/resource_adaptive/bin/python -u "${RUNNER}" \
    "${RUN}/top5.json" "${REPLAY}" "${BASELINE}"

test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = \
    f8f16a0c8ed261e2158fa5c54f061ebb4ce3aa45e6eab816c9db40bf40ab588a
jq -e '
    .real_workload_invocation_count == 5 and
    (.successful | length) == 5 and
    (.technical_failures | length) == 0
' "${LEDGER}" >/dev/null
jq -e '
    .successful_real_replay_count == 5 and
    .technical_failure_count == 0 and
    .baseline_params_restored == true and
    .baseline_snapshot_restored == true and
    .accepted_count_delta == 0 and
    .incumbent_updated == false and
    .range_updated == false and
    .gradient_update_applied == false and
    .state_commit_eligible == false
' "${SUMMARY}" >/dev/null
printf 'TPCC_REPEAT51_COMPLETE selection_sha=%s\n' \
    "$(sha256sum "${SELECTION}" | awk '{print $1}')"
