#!/usr/bin/env bash
set -euo pipefail

# Deterministic state-neutral repeat of the five clean diagnostic observations.
# The first observation supplies the plan-local resource estimate; this batch
# measures whether that estimate and the induced ranking repeat independently.
B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
P=${B}/job_round8_corrected_top5_v6_20260731
SOURCE=${T}/diagnostic_batch002
TARGET=${T}/diagnostic_validation_batch003
BUILDER=/tmp/build_job_exact_plan_repeat_validation.py
RUNNER=${P}/run_job_round8_strict_serial.py
STATE=${T}/package/formal_state.json
SELECTION=${TARGET}/top5.json

EXPECTED_BUILDER_SHA=89c3a2238830087c129cadee76cd5a28248d87ce10b0fd98c6d5f9cb62bdd6cf
EXPECTED_SOURCE_SELECTION_SHA=9eba280b46047e4b2dd7998b8b8a7e5489231e2a3defb8f8c8ffba856e2ca025
EXPECTED_SOURCE_SUMMARY_SHA=7b14647baccc1609a99f36df9904d176f4abbce9b15f6db8155bb561c56bcd20
EXPECTED_RUNNER_SHA=9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
EXPECTED_STATE_SHA=4d18a6e5b4049f5890d0ac3602f3a3bfdba38a39db5d22c819734130575c601b

test "$(sha256sum "${BUILDER}" | awk '{print $1}')" = "${EXPECTED_BUILDER_SHA}"
test "$(sha256sum "${SOURCE}/top5.json" | awk '{print $1}')" = "${EXPECTED_SOURCE_SELECTION_SHA}"
test "$(sha256sum "${SOURCE}/replay/summary.json" | awk '{print $1}')" = "${EXPECTED_SOURCE_SUMMARY_SHA}"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"

jq -e '
  .diagnostic_only == true and
  .physical_invocation_count == 5 and
  .successful_real_replay_count == 5 and
  .technical_failure_count == 0 and
  .baseline_params_restored == true and
  .baseline_restore.success == true and
  .initial_state == .final_state and
  .formal_update_flags == {
    "accepted": false,
    "gradient": false,
    "incumbent": false,
    "range": false
  }
' "${SOURCE}/replay/summary.json" >/dev/null

mkdir -p "${TARGET}"
exec 9>"${TARGET}.lock"
flock -n 9
test ! -e "${SELECTION}"
test ! -e "${TARGET}/replay"

python3 -u "${BUILDER}" \
  --selection "${SOURCE}/top5.json" \
  --summary "${SOURCE}/replay/summary.json" \
  --output "${SELECTION}"
python3 -u "${BUILDER}" \
  --selection "${SOURCE}/top5.json" \
  --summary "${SOURCE}/replay/summary.json" \
  --output "${TARGET}/top5.second.json"
cmp "${SELECTION}" "${TARGET}/top5.second.json"

jq -e --slurpfile source "${SOURCE}/top5.json" '
  .successor_generation_contract == "job_exact_plan_repeat_validation_v1" and
  .diagnostic_only == true and
  .trusted_count == 3 and .probe_count == 2 and
  .novel_top5_fingerprint_count == 5 and
  (.top5 | length) == 5 and
  ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
  (([.top5[].three_level_fingerprint.id] -
    [$source[0].top5[].three_level_fingerprint.id]) | length) == 5 and
  ([.top5[] | select(.selection_role == "trusted")] | length) == 3 and
  ([.top5[] | select(.selection_role == "probe")] | length) == 2 and
  all(.top5[];
    .boundary_corner == false and
    .three_level_fingerprint.valid == true and
    .accepted == false and
    .gradient_update_applied == false and
    .incumbent_update_applied == false and
    .range_update_applied == false and
    has("feature_ratio") and has("predicted_resource") and
    has("utilization") and has("dScore/dU") and has("T") and
    has("confidence") and has("g_j") and has("eta") and
    has("delta_param") and has("candidate_score"))
' "${SELECTION}" >/dev/null

sha256sum "${SELECTION}" "${BUILDER}" "${RUNNER}" "${STATE}" > "${TARGET}/sealed.sha256"
python3 -u "${RUNNER}" \
  "${SELECTION}" "${TARGET}/replay" \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --state "${STATE}" --accepted-target 10 \
  --diagnostic-only --dry-run > "${TARGET}/dry_run.json"

test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
exec python3 -u "${RUNNER}" \
  "${SELECTION}" "${TARGET}/replay" \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --state "${STATE}" --accepted-target 10 \
  --diagnostic-only
