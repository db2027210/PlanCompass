#!/usr/bin/env bash
set -euo pipefail

# State-neutral acquisition after the fresh10 successor has no positive LCB.
# This script is sealed to the exact post-batch001 formal state and cannot
# mutate accepted/incumbent/range/gradient state.
B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
P=${B}/job_round8_corrected_top5_v6_20260731
TARGET=${T}/diagnostic_batch002
GENERATOR=${T}/package/job_exact_regime_successor_expand.py
RUNNER=${P}/run_job_round8_strict_serial.py
STATE=${T}/package/formal_state.json
FAILURE=${T}/successor_batch002_top5.json.failure_audit.json
SELECTION=${TARGET}/top5.json

EXPECTED_GENERATOR_SHA=a03c2264cdfe2b26a8bb4a5bcbc5648498ce68020806d6d4a84e3cb592b51da5
EXPECTED_RUNNER_SHA=9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
EXPECTED_STATE_SHA=4d18a6e5b4049f5890d0ac3602f3a3bfdba38a39db5d22c819734130575c601b
EXPECTED_FAILURE_SHA=562f886139a3fb203ff5f62cb1c91fcbd9bc0e8bcffc986e552e54742de48ff9

test "$(sha256sum "${GENERATOR}" | awk '{print $1}')" = "${EXPECTED_GENERATOR_SHA}"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
test "$(sha256sum "${FAILURE}" | awk '{print $1}')" = "${EXPECTED_FAILURE_SHA}"
jq -e '
  .status == "fail_closed" and
  .reason == "no_positive_trusted_improvement" and
  .recommended_next_mode == "diagnostic_force_probe_discovery" and
  .new_workload_calls == 0 and
  .trusted_count == 3 and .probe_count == 5
' "${FAILURE}" >/dev/null

mkdir -p "${TARGET}"
exec 9>"${TARGET}.lock"
flock -n 9
test ! -e "${SELECTION}"
test ! -e "${TARGET}/replay"

python3 -u "${GENERATOR}" \
  --history "${P}/authority/ranking_analysis.json" \
  --evidence-top5 "${T}/package/top5_promoted_formal.json" \
  --events "${T}/batch001/events.jsonl" \
  --formal-state "${STATE}" \
  --incumbent-events "${T}/batch001/events.jsonl" \
  --config "${B}/job_batch02_exact_regime_calibration_v1_20260731T0332/remote_config.json" \
  --capture "${B}/job_batch02_exact_regime_calibration_v1_20260731T0332/baseline_plan_capture.json" \
  --incumbent-point-result "${B}/formal_points/point_0333/point_result.json" \
  --code-root "${B}/gradient_debug_round7_fresh_exclusion_v1/code" \
  --exclude "${B}/job_score_span_repeat_validation_v35_20260731T1940/package/top5.json" \
  --exclude "${T}/package/top5_promoted_formal.json" \
  --mode diagnostic \
  --force-probe-discovery \
  --output "${SELECTION}"

jq -e '
  .diagnostic_only == true and
  .formal_acquisition.mode == "diagnostic" and
  .trusted_count == 0 and .probe_count == 5 and
  .novel_top5_fingerprint_count == 5 and
  (.top5 | length) == 5 and
  ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
  all(.top5[];
    .selection_role == "probe" and
    .boundary_corner == false and
    .three_level_fingerprint.valid == true and
    has("feature_ratio") and has("predicted_resource") and
    has("utilization") and has("dScore/dU") and has("T") and
    has("confidence") and has("g_j") and has("eta") and
    has("delta_param") and has("candidate_score"))
' "${SELECTION}" >/dev/null
sha256sum "${SELECTION}" > "${TARGET}/selection.sha256"

python3 -u "${RUNNER}" \
  "${SELECTION}" "${TARGET}/replay" \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --state "${STATE}" --accepted-target 10 \
  --diagnostic-only --dry-run > "${TARGET}/dry_run.json"

test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
python3 -u "${RUNNER}" \
  "${SELECTION}" "${TARGET}/replay" \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --state "${STATE}" --accepted-target 10 \
  --diagnostic-only

test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
