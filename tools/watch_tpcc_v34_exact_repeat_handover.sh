#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
CAMPAIGN=${PROJECT}/history/tpcc_final_algorithm_fresh10_v29_20260731T2005
CONTROL=${CAMPAIGN}/autonomous_controller_v32
STATE=${CONTROL}/controller_state.json
FORMAL_STATE=${CAMPAIGN}/formal_state.json
SOURCE_MANIFEST=/tmp/tpcc_final_fresh0_autonomous_v33_20260731/manifest.json
PACKAGE=/tmp/tpcc_final_fresh0_autonomous_v34_20260731
PACKAGER=${PROJECT}/tools/package_tpcc_rankability_v33_resume.py
OLD_PID=3890364
RUN=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/formal10_final_algorithm_fresh10_v29_20260731T2005/autonomous_runs_v32/autonomous_diagnostic_09
SUMMARY=${RUN}/top5_real_replay_round12_diagnostic_v1/summary.json
LEDGER=${RUN}/strict_top5_real_replay_round12_ledger_v1.json
MARKER=${CONTROL}/prepare_10/hold_exact_repeat_v34.json
QUARANTINE=${CONTROL}/hold_exact_repeat_v34.json
LOG=${CAMPAIGN}/v34_exact_repeat_handover.log

EXPECTED_SOURCE_MANIFEST_SHA=73fd5ba7b8ace8f78357dcfa4466b8c1ff01c14a4d3976165999936b5e31763c
EXPECTED_PACKAGER_SHA=404d83db9682a0b0040605982bc3ab132d1cc3e8451f0b5e2815049787077854
EXPECTED_CONTROLLER_SHA=6e099b67d443393285048690c81d53f8b1e06ca7f482ec929e474d237076e2b1
EXPECTED_REPEAT_BUILDER_SHA=0ac5709555452a27a16515955ed21b7dcfe87bb64b8ac94f7e9da7e7f38a74e2
EXPECTED_BUILDER_SHA=52796ab4271dc1832fc565f0ad3973b27b91e3b5540888294c8a0e2b4610b56b
EXPECTED_ROBUST_BUILDER_SHA=0aec54299ceb364208e85fb14cb97fe2f09983cdf4715b10d623feee11ae38c3
EXPECTED_MARKER_SHA=aaa70446046d5917c64efe4241093b2043decac65aae481708d70933071d8e8e
EXPECTED_FORMAL_STATE_SHA=dec2f7993b14105e3b21a791481982a78736b7050b6b4f370e6fc9423b79c4d9

exec 9>"${CAMPAIGN}/v34_exact_repeat_handover.lock"
flock -n 9
exec > >(tee -a "${LOG}") 2>&1

test "$(sha256sum "${SOURCE_MANIFEST}" | awk '{print $1}')" = "${EXPECTED_SOURCE_MANIFEST_SHA}"
test "$(sha256sum "${PACKAGER}" | awk '{print $1}')" = "${EXPECTED_PACKAGER_SHA}"
test "$(sha256sum "${PROJECT}/tools/tpcc_autonomous_target10_controller_v23.py" | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA}"
test "$(sha256sum "${PROJECT}/tools/build_tpcc_exact_plan_repeat_validation.py" | awk '{print $1}')" = "${EXPECTED_REPEAT_BUILDER_SHA}"
test "$(sha256sum "${PROJECT}/tools/build_tpcc_autonomous_next_top5_v23.py" | awk '{print $1}')" = "${EXPECTED_BUILDER_SHA}"
test "$(sha256sum "${PROJECT}/tools/build_tpcc_round14_robust_regime_top5.py" | awk '{print $1}')" = "${EXPECTED_ROBUST_BUILDER_SHA}"
test "$(sha256sum "${MARKER}" | awk '{print $1}')" = "${EXPECTED_MARKER_SHA}"
test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = "${EXPECTED_FORMAL_STATE_SHA}"
test ! -e "${PACKAGE}"
test ! -e "${QUARANTINE}"

echo "waiting for diagnostic_09 and the zero-call prepare_10 boundary"
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
  .next_ordinal == 10 and
  .next_mode == "diagnostic_validation" and
  .audits[-1].mode == "diagnostic" and
  .audits[-1].clean == true and
  .audits[-1].physical_calls == 5 and
  .audits[-1].valid_replays == 5 and
  .audits[-1].technical_failures == 0 and
  .formal_state_sha256 == "dec2f7993b14105e3b21a791481982a78736b7050b6b4f370e6fc9423b79c4d9"
' "${STATE}" >/dev/null
jq -e '.accepted_count == 1' "${FORMAL_STATE}" >/dev/null
test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = "${EXPECTED_FORMAL_STATE_SHA}"

# Archive the deliberate fail-closed marker.  The replacement controller will
# resume the same empty ordinal and deterministically build the exact repeat.
mv "${MARKER}" "${QUARANTINE}"
test "$(sha256sum "${QUARANTINE}" | awk '{print $1}')" = "${EXPECTED_MARKER_SHA}"
test -z "$(find "${CONTROL}/prepare_10" -mindepth 1 -maxdepth 1 -print -quit)"

/home/anonymous/miniconda3/envs/resource_adaptive/bin/python -u "${PACKAGER}" \
  --source-manifest "${SOURCE_MANIFEST}" \
  --package "${PACKAGE}"
jq -e '
  .status == "sealed_resume_no_workload_started" and
  .accepted_count == 1
' "${PACKAGE}/package_manifest.json" >/dev/null
jq -e --arg repeat "${PROJECT}/tools/build_tpcc_exact_plan_repeat_validation.py" '
  .exact_repeat_builder_path == $repeat and
  .rankability_fix_contract == "clean_probe5_then_exact_point_repeat_then_gate_v2"
' "${PACKAGE}/manifest.json" >/dev/null
sha256sum "${SOURCE_MANIFEST}" "${STATE}" "${FORMAL_STATE}" \
  "${PACKAGE}/manifest.json" "${PACKAGE}/launch_armed_foreground.sh" \
  > "${PACKAGE}/handover_seal.sha256"

exec "${PACKAGE}/launch_armed_foreground.sh"
