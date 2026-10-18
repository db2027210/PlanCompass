#!/usr/bin/env bash
set -euo pipefail

B=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726
CONTROL=/home/anonymous/program/resource_adaptive/history/tpcc_autonomous_target10_controller_v12_20260731
PACKAGE=/tmp/tpcc_autonomous_target10_package_v23_20260731
SOURCE_RUN=${B}/autonomous_target10_controller_v4/runs/autonomous_diagnostic_validation_48
SOURCE_SELECTION=${CONTROL}/prepare_48/sealed_run/top5.json
SOURCE_LEDGER=${SOURCE_RUN}/strict_top5_real_replay_round14_ledger_v1.json
SOURCE_SUMMARY=${SOURCE_RUN}/top5_real_replay_round14_diagnostic_v1/summary.json
PREPARE=${CONTROL}/prepare_49
SEALED=${PREPARE}/sealed_run
SELECTION=${SEALED}/top5.json
RUN=${B}/autonomous_target10_controller_v4/runs/autonomous_diagnostic_validation_49_exact_repeat_v26
REPLAY=${RUN}/top5_real_replay_round14_diagnostic_v1
LEDGER=${RUN}/strict_top5_real_replay_round14_ledger_v1.json
SUMMARY=${REPLAY}/summary.json
FORMAL_STATE=${B}/formal10_tpcc_state.json
BASELINE=${B}/candidate_measurements/point_20260729_022349_1052396/summary.json
RUNNER=${B}/gradient_debug_round14_robust_regime_interval_v2_20260729T1824/run_tpcc_round11_calibrated_top5_strict_serial.py
BUILDER=/home/anonymous/program/resource_adaptive/tools/build_tpcc_exact_plan_repeat_validation.py
CONTROLLER_STATE=${CONTROL}/controller_state.json
OLD_CONTROLLER_PID=${TPCC_OLD_CONTROLLER_PID:-2842639}
LOG=${CONTROL}/validation48_repeat49_handover_v24.log

EXPECTED_SOURCE_SELECTION_SHA=5912435038e1cffb06e5fad9a05310cedf8d1d94a5b526e61b26c87e10b717c0
EXPECTED_FORMAL_STATE_SHA=f8f16a0c8ed261e2158fa5c54f061ebb4ce3aa45e6eab816c9db40bf40ab588a
EXPECTED_BASELINE_SHA=9c0f0d083796bef1ec8d49c4aa71b1d8e904fca43dbfb49a1708c0c40ccd7356
EXPECTED_RUNNER_SHA=df1f9f9b43656214cdccace0e3614fada7d2efc7c7679942da6478ac0f042d68
EXPECTED_BUILDER_SHA=0646667c172cbbd5925611609b1aa2c7207edf3c088c880f32ce2b90f79c4c6d
EXPECTED_CONTROLLER_SHA=692bf7196678a455867e2bc9db05009c242d0ac66498734cab59500def23c8ba
EXPECTED_MANIFEST_SHA=070fb92fc195b7e8c8ebe2d99d6c70238f32c142fbd33612c290eb211a53770f
EXPECTED_LAUNCH_SHA=738f2c2f6fab07498d13748e03af480c88f291683b2bc9fe9bbb12ac221455a7

exec > >(tee -a "${LOG}") 2>&1
echo "waiting for v23 controller pid=${OLD_CONTROLLER_PID} to fail closed at prepare_49 barrier"
while kill -0 "${OLD_CONTROLLER_PID}" 2>/dev/null; do
    sleep 5
done

test "$(sha256sum "${SOURCE_SELECTION}" | awk '{print $1}')" = "${EXPECTED_SOURCE_SELECTION_SHA}"
test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = "${EXPECTED_FORMAL_STATE_SHA}"
test "$(sha256sum "${BASELINE}" | awk '{print $1}')" = "${EXPECTED_BASELINE_SHA}"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${BUILDER}" | awk '{print $1}')" = "${EXPECTED_BUILDER_SHA}"
test "$(sha256sum "${PACKAGE}/tpcc_autonomous_target10_controller_v23.py" | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA}"
test "$(sha256sum "${PACKAGE}/manifest.json" | awk '{print $1}')" = "${EXPECTED_MANIFEST_SHA}"
test "$(sha256sum "${PACKAGE}/launch_armed_foreground.sh" | awk '{print $1}')" = "${EXPECTED_LAUNCH_SHA}"

jq -e '
    .real_workload_invocation_count == 5 and
    (.successful | length) == 5 and
    (.technical_failures | length) == 0
' "${SOURCE_LEDGER}" >/dev/null
jq -e '
    .real_workload_invocation_count == 5 and
    .successful_real_replay_count == 5 and
    .technical_failure_count == 0 and
    .baseline_params_restored == true and
    .baseline_snapshot_restored == true and
    .accepted_count_delta == 0 and
    .incumbent_updated == false and
    .range_updated == false and
    .gradient_update_applied == false and
    .state_commit_eligible == false
' "${SOURCE_SUMMARY}" >/dev/null

# v23 must have audited validation48, observed the rank failure, and stopped
# before preparing any next workload.  The marker itself is the fail-closed
# proof; it is archived rather than deleted.
jq -e --arg selection_sha "${EXPECTED_SOURCE_SELECTION_SHA}" '
    .current_run == null and
    .next_ordinal == 49 and
    .next_mode == "diagnostic" and
    .last_quality_gate.passed == false and
    .last_quality_gate.checks.trusted_spearman_positive == false and
    .last_quality_gate.checks.top5_clean_no_technical_failure == true and
    .audits[-1].mode == "diagnostic_validation" and
    .audits[-1].selection_sha256 == $selection_sha and
    .audits[-1].clean == true
' "${CONTROLLER_STATE}" >/dev/null
mkdir -p "${PREPARE}/handover_archive"
if test -f "${PREPARE}/hold_exact_plan_repeat_v24"; then
    mv "${PREPARE}/hold_exact_plan_repeat_v24" \
       "${PREPARE}/handover_archive/hold_exact_plan_repeat_v24"
else
    test -f "${PREPARE}/handover_archive/hold_exact_plan_repeat_v24"
fi

test ! -e "${RUN}"
mkdir -p "${SEALED}"
SECOND=${PREPARE}/top5.second.json
python3 -u "${BUILDER}" \
    --selection "${SOURCE_SELECTION}" \
    --ledger "${SOURCE_LEDGER}" \
    --output "${SELECTION}"
python3 -u "${BUILDER}" \
    --selection "${SOURCE_SELECTION}" \
    --ledger "${SOURCE_LEDGER}" \
    --output "${SECOND}"
cmp "${SELECTION}" "${SECOND}"

/home/anonymous/miniconda3/envs/resource_adaptive/bin/python - \
    "${PACKAGE}/tpcc_autonomous_target10_controller_v23.py" \
    "${SELECTION}" <<'PY'
import importlib.util
import sys
from pathlib import Path

controller_path = Path(sys.argv[1])
selection_path = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("tpcc_controller_v23", controller_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.validate_selection(module.load_json(selection_path), mode="diagnostic_validation")
print("TPCC_REPEAT49_PREFLIGHT_OK", flush=True)
PY

SELECTION_SHA=$(sha256sum "${SELECTION}" | awk '{print $1}')
sha256sum "${SELECTION}" "${BUILDER}" "${RUNNER}" "${FORMAL_STATE}" > "${SEALED}/sealed.sha256"
mkdir -p "${RUN}"
cp "${SELECTION}" "${RUN}/top5.json"
test "$(sha256sum "${RUN}/top5.json" | awk '{print $1}')" = "${SELECTION_SHA}"

PYTHONPATH=/home/anonymous/program/resource_adaptive:${B}/gradient_debug_round14_robust_regime_interval_v2_20260729T1824 \
    /home/anonymous/miniconda3/envs/resource_adaptive/bin/python -u "${RUNNER}" \
    "${RUN}/top5.json" "${REPLAY}" "${BASELINE}"

test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = "${EXPECTED_FORMAL_STATE_SHA}"
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

LEDGER_SHA=$(sha256sum "${LEDGER}" | awk '{print $1}')
SUMMARY_SHA=$(sha256sum "${SUMMARY}" | awk '{print $1}')
jq \
    --arg root "${RUN}" \
    --arg selection "${SELECTION}" \
    --arg selection_sha "${SELECTION_SHA}" \
    --arg ledger "${LEDGER}" \
    --arg ledger_sha "${LEDGER_SHA}" \
    --arg summary "${SUMMARY}" \
    --arg summary_sha "${SUMMARY_SHA}" \
    --arg state_sha "${EXPECTED_FORMAL_STATE_SHA}" \
    '
    .current_run = {
        "mode": "diagnostic_validation",
        "root": $root,
        "selection_path": $selection,
        "selection_sha256": $selection_sha,
        "ledger_path": $ledger,
        "ledger_sha256": $ledger_sha,
        "summary_path": $summary,
        "summary_sha256": $summary_sha,
        "formal_state_sha256_before": $state_sha
    } |
    .next_ordinal = 50 |
    .status = "auditing_injected_exact_plan_repeat49" |
    .formal_state_sha256 = $state_sha
    ' "${CONTROLLER_STATE}" > "${CONTROLLER_STATE}.tmp"
mv "${CONTROLLER_STATE}.tmp" "${CONTROLLER_STATE}"

exec "${PACKAGE}/launch_armed_foreground.sh"
