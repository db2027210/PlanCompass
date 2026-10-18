#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
CAMPAIGN=${PROJECT}/history/tpcc_final_algorithm_fresh10_v29_20260731T2005
ROOT=${CAMPAIGN}/autonomous_controller_v32
STATE=${ROOT}/controller_state.json
FORMAL_STATE=${CAMPAIGN}/formal_state.json
SOURCE_MANIFEST=/tmp/tpcc_final_fresh0_autonomous_v32_20260731/manifest.json
PACKAGE=/tmp/tpcc_final_fresh0_autonomous_v33_20260731
PACKAGER=${PROJECT}/tools/package_tpcc_rankability_v33_resume.py
OLD_PID=3536526
RUN=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/formal10_final_algorithm_fresh10_v29_20260731T2005/autonomous_runs_v32/autonomous_diagnostic_06
SUMMARY=${RUN}/top5_real_replay_round12_diagnostic_v1/summary.json
LEDGER=${RUN}/strict_top5_real_replay_round12_ledger_v1.json
MARKER=${ROOT}/prepare_07/v33_handover.marker
QUARANTINE=${ROOT}/v33_handover_prepare07.marker
LOG=${CAMPAIGN}/v33_handover_controller.log

EXPECTED_SOURCE_MANIFEST_SHA=7de2a69cd79287743d463c4828863bb5bd0b00ac613c7b14630c956691df1997
EXPECTED_PACKAGER_SHA=97723d3e5e1c747c511579e324eb2efd6e620df2581d1154bcfffb3c58927eb4
EXPECTED_BUILDER_SHA=52796ab4271dc1832fc565f0ad3973b27b91e3b5540888294c8a0e2b4610b56b
EXPECTED_ROBUST_BUILDER_SHA=0aec54299ceb364208e85fb14cb97fe2f09983cdf4715b10d623feee11ae38c3
EXPECTED_CONTROLLER_SHA=54d5c9d123dca3e68ff30c39e62ba015fca9eb5cf1163bc244c95f8bb0b767f5
EXPECTED_MARKER_SHA=b2bb3efc25c615c0544d5af036269a7b10eb1336d4c5f8ed8d052898edab9700
EXPECTED_FORMAL_STATE_SHA=dec2f7993b14105e3b21a791481982a78736b7050b6b4f370e6fc9423b79c4d9

exec 9>"${CAMPAIGN}/v33_handover.lock"
flock -n 9
exec > >(tee -a "${LOG}") 2>&1
test "$(sha256sum "${SOURCE_MANIFEST}" | awk '{print $1}')" = "${EXPECTED_SOURCE_MANIFEST_SHA}"
test "$(sha256sum "${PACKAGER}" | awk '{print $1}')" = "${EXPECTED_PACKAGER_SHA}"
test "$(sha256sum "${PROJECT}/tools/build_tpcc_autonomous_next_top5_v23.py" | awk '{print $1}')" = "${EXPECTED_BUILDER_SHA}"
test "$(sha256sum "${PROJECT}/tools/build_tpcc_round14_robust_regime_top5.py" | awk '{print $1}')" = "${EXPECTED_ROBUST_BUILDER_SHA}"
test "$(sha256sum "${PROJECT}/tools/tpcc_autonomous_target10_controller_v23.py" | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA}"
test "$(sha256sum "${MARKER}" | awk '{print $1}')" = "${EXPECTED_MARKER_SHA}"
test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = "${EXPECTED_FORMAL_STATE_SHA}"
test ! -e "${PACKAGE}"
test ! -e "${QUARANTINE}"

while kill -0 "${OLD_PID}" 2>/dev/null; do
  sleep 10
done

test -f "${SUMMARY}"
test -f "${LEDGER}"
jq -e '
  .state_commit_eligible == false and
  .accepted_count_delta == 0 and
  .incumbent_updated == false and
  .range_updated == false and
  .gradient_update_applied == false and
  .baseline_params_restored == true and
  .baseline_snapshot_restored == true
' "${SUMMARY}" >/dev/null
jq -e '
  .real_workload_invocation_count == 5 and
  (.successful | length) == 5 and
  (.technical_failures | length) == 0
' "${LEDGER}" >/dev/null
jq -e '
  .current_run == null and
  .next_ordinal == 7 and
  .formal_state_sha256 ==
    "dec2f7993b14105e3b21a791481982a78736b7050b6b4f370e6fc9423b79c4d9"
' "${STATE}" >/dev/null
jq -e '.accepted_count == 1' "${FORMAL_STATE}" >/dev/null
test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = "${EXPECTED_FORMAL_STATE_SHA}"

# Preserve the fail-closed marker as audit evidence, then let v33 resume the
# empty preparation directory at exactly the same ordinal.
mv "${MARKER}" "${QUARANTINE}"
test "$(sha256sum "${QUARANTINE}" | awk '{print $1}')" = "${EXPECTED_MARKER_SHA}"
test -z "$(find "${ROOT}/prepare_07" -mindepth 1 -maxdepth 1 -print -quit)"

/home/anonymous/miniconda3/envs/resource_adaptive/bin/python -u "${PACKAGER}" \
  --source-manifest "${SOURCE_MANIFEST}" \
  --package "${PACKAGE}"
jq -e '
  .status == "sealed_resume_no_workload_started" and
  .accepted_count == 1
' "${PACKAGE}/package_manifest.json" >/dev/null
sha256sum "${SOURCE_MANIFEST}" "${STATE}" "${FORMAL_STATE}" \
  "${PACKAGE}/manifest.json" "${PACKAGE}/launch_armed_foreground.sh" \
  > "${PACKAGE}/handover_seal.sha256"

exec "${PACKAGE}/launch_armed_foreground.sh"
