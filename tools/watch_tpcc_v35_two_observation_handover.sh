#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
CAMPAIGN=${PROJECT}/history/tpcc_final_algorithm_fresh10_v29_20260731T2005
CONTROL=${CAMPAIGN}/autonomous_controller_v32
STATE=${CONTROL}/controller_state.json
FORMAL_STATE=${CAMPAIGN}/formal_state.json
SOURCE_MANIFEST=/tmp/tpcc_final_fresh0_autonomous_v34_20260731/manifest.json
PACKAGE=/tmp/tpcc_final_fresh0_autonomous_v35_20260801
PACKAGER=${PROJECT}/tools/package_tpcc_rankability_v33_resume.py
RUN=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/formal10_final_algorithm_fresh10_v29_20260731T2005/autonomous_runs_v32/autonomous_diagnostic_validation_34
SUMMARY=${RUN}/top5_real_replay_round14_diagnostic_v1/summary.json
LEDGER=${RUN}/strict_top5_real_replay_round14_ledger_v1.json
MARKER=${CONTROL}/prepare_35/v35_two_observation_handover.marker
QUARANTINE=${CONTROL}/v35_two_observation_handover.marker
LOG=${CAMPAIGN}/v35_two_observation_handover.log

EXPECTED_SOURCE_MANIFEST_SHA=77d44bf9871b92221c06f5a717ca8742a2396b0a7ebf5b6cdb56795947905c5f
EXPECTED_PACKAGER_SHA=bfe8406ad900c574d8d9b04370acffd763cfbea9c89cfff1fbcd3be1356ae3aa
EXPECTED_CONTROLLER_SHA=503d959fc3136870ae53ee77737b33f7317aa11cb61bf49c8be334847882b3ed
EXPECTED_MARKER_SHA=9aa12175e125aacaa038d290cd0e1731197bbcd081b6fd576bce8509247a6cf8
EXPECTED_FORMAL_STATE_SHA=9944cbe9cb17dbdfedb78de12ccce39a35f08b1096b39fc8ecb22258bedbce89

exec 9>"${CAMPAIGN}/v35_two_observation_handover.lock"
flock -n 9
exec > >(tee -a "${LOG}") 2>&1

test "$(sha256sum "${SOURCE_MANIFEST}" | awk '{print $1}')" = "${EXPECTED_SOURCE_MANIFEST_SHA}"
test "$(sha256sum "${PACKAGER}" | awk '{print $1}')" = "${EXPECTED_PACKAGER_SHA}"
test "$(sha256sum "${PROJECT}/tools/tpcc_autonomous_target10_controller_v23.py" | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA}"
if [[ -f "${MARKER}" && ! -e "${QUARANTINE}" ]]; then
  test "$(sha256sum "${MARKER}" | awk '{print $1}')" = "${EXPECTED_MARKER_SHA}"
elif [[ -f "${QUARANTINE}" && ! -e "${MARKER}" ]]; then
  test "$(sha256sum "${QUARANTINE}" | awk '{print $1}')" = "${EXPECTED_MARKER_SHA}"
else
  echo "handover marker must exist in exactly one expected location" >&2
  exit 1
fi
test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = "${EXPECTED_FORMAL_STATE_SHA}"
test ! -e "${PACKAGE}"

echo "waiting for validation34 evidence and the zero-call prepare35 boundary"
while [[ ! -f "${SUMMARY}" || ! -f "${LEDGER}" ]]; do
  sleep 10
done

while ! jq -e '
  .current_run == null and
  .next_ordinal == 35 and
  .audits[-1].mode == "diagnostic_validation"
' "${STATE}" >/dev/null 2>&1; do
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
  .baseline_snapshot_restored == true and
  .successful_real_replay_count == 5 and
  .technical_failure_count == 0
' "${SUMMARY}" >/dev/null
jq -e '
  .real_workload_invocation_count == 5 and
  (.successful | length) == 5 and
  (.technical_failures | length) == 0
' "${LEDGER}" >/dev/null
jq -e '
  .current_run == null and
  .next_ordinal == 35 and
  .audits[-1].mode == "diagnostic_validation" and
  .audits[-1].clean == true and
  .audits[-1].physical_calls == 5 and
  .audits[-1].valid_replays == 5 and
  .audits[-1].technical_failures == 0 and
  .formal_state_sha256 == "9944cbe9cb17dbdfedb78de12ccce39a35f08b1096b39fc8ecb22258bedbce89"
' "${STATE}" >/dev/null
jq -e '.accepted_count == 2' "${FORMAL_STATE}" >/dev/null
test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = "${EXPECTED_FORMAL_STATE_SHA}"

if [[ -f "${MARKER}" ]]; then
  mv "${MARKER}" "${QUARANTINE}"
fi
test "$(sha256sum "${QUARANTINE}" | awk '{print $1}')" = "${EXPECTED_MARKER_SHA}"
test -z "$(find "${CONTROL}/prepare_35" -mindepth 1 -maxdepth 1 -print -quit)"

/home/anonymous/miniconda3/envs/resource_adaptive/bin/python \
  "${PROJECT}/tools/package_tpcc_rankability_v33_resume.py" \
  --source-manifest "${SOURCE_MANIFEST}" \
  --package "${PACKAGE}"
jq -e '
  .status == "sealed_resume_no_workload_started" and
  .accepted_count == 2
' "${PACKAGE}/package_manifest.json" >/dev/null
jq -e --arg contract "two_independent_exact_point_componentwise_median_interval_v1" '
  .formal_prediction_contract == $contract and
  .rankability_fix_contract == "clean_probe5_then_exact_point_repeat_then_two_observation_median_formal_v3"
' "${PACKAGE}/manifest.json" >/dev/null
sha256sum "${SOURCE_MANIFEST}" "${STATE}" "${FORMAL_STATE}" \
  "${PACKAGE}/manifest.json" "${PACKAGE}/launch_armed_foreground.sh" \
  > "${PACKAGE}/handover_seal.sha256"

exec "${PACKAGE}/launch_armed_foreground.sh"
