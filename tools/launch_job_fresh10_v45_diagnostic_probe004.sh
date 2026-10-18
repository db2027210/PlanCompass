#!/usr/bin/env bash
set -euo pipefail

# Automatic state-neutral discovery after formal batch003 cleanly rejected all
# candidates. No candidate is hand-selected and no formal state may change in
# this batch.
B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
P=${B}/job_round8_corrected_top5_v6_20260731
SOURCE=${T}/autonomous_v43/formal_batch003
TARGET=${T}/autonomous_v45/diagnostic_probe004
GENERATOR=${T}/package/job_exact_regime_successor_expand.py
RUNNER=${P}/run_job_round8_strict_serial.py
STATE=${T}/package/formal_state.json
SELECTION=${TARGET}/top5.json

EXPECTED_GENERATOR_SHA=a03c2264cdfe2b26a8bb4a5bcbc5648498ce68020806d6d4a84e3cb592b51da5
EXPECTED_RUNNER_SHA=9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
# Formal batch003 completed with five clean rejections and atomically advanced
# only the physical/formal counters recorded in the formal state.
EXPECTED_STATE_SHA=be1b8fecccd9a1092b0d49c7bfb76b86f5d61fab5031387169250b5380606ef4

test "$(sha256sum "${GENERATOR}" | awk '{print $1}')" = "${EXPECTED_GENERATOR_SHA}"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
jq -e '
  .diagnostic_only == false and
  .physical_invocation_count == 5 and
  .successful_real_replay_count == 5 and
  .technical_failure_count == 0 and
  .baseline_params_restored == true and
  ([.successful[] | select(.accepted == true)] | length) == 0 and
  all(.successful[];
    .accepted == false and
    .gradient_update_applied == false and
    .incumbent_update_applied == false and
    .range_update_applied == false)
' "${SOURCE}/replay/summary.json" >/dev/null
test "$(tr -d '[:space:]' < "${B}/formal_search_accepted_counter.txt")" = 2

mkdir -p "${TARGET}"
exec 9>"${TARGET}.lock"
flock -n 9
test ! -e "${SELECTION}"
test ! -e "${TARGET}/replay"

python3 -u "${GENERATOR}" \
  --history "${P}/authority/ranking_analysis.json" \
  --evidence-top5 "${SOURCE}/package/top5_promoted_formal.json" \
  --events "${SOURCE}/replay/events.jsonl" \
  --formal-state "${STATE}" \
  --incumbent-events "${T}/batch001/events.jsonl" \
  --incumbent-events "${T}/formal_batch002_promoted_validation004/replay/events.jsonl" \
  --incumbent-events "${SOURCE}/replay/events.jsonl" \
  --config "${B}/job_batch02_exact_regime_calibration_v1_20260731T0332/remote_config.json" \
  --capture "${B}/job_batch02_exact_regime_calibration_v1_20260731T0332/baseline_plan_capture.json" \
  --incumbent-point-result "${B}/formal_points/point_0333/point_result.json" \
  --code-root "${B}/gradient_debug_round7_fresh_exclusion_v1/code" \
  --exclude "${B}/job_score_span_repeat_validation_v35_20260731T1940/package/top5.json" \
  --exclude "${T}/package/top5_promoted_formal.json" \
  --exclude "${T}/diagnostic_batch002/top5.json" \
  --exclude "${T}/diagnostic_validation_batch003/top5.json" \
  --exclude "${T}/diagnostic_validation_batch004/top5.json" \
  --exclude "${T}/formal_batch002_promoted_validation004/package/top5_promoted_formal.json" \
  --exclude "${T}/autonomous_v43/diagnostic_probe003/top5.json" \
  --exclude "${SOURCE}/package/top5_promoted_formal.json" \
  --mode diagnostic \
  --force-probe-discovery \
  --output "${SELECTION}"

python3 -u "${GENERATOR}" \
  --history "${P}/authority/ranking_analysis.json" \
  --evidence-top5 "${SOURCE}/package/top5_promoted_formal.json" \
  --events "${SOURCE}/replay/events.jsonl" \
  --formal-state "${STATE}" \
  --incumbent-events "${T}/batch001/events.jsonl" \
  --incumbent-events "${T}/formal_batch002_promoted_validation004/replay/events.jsonl" \
  --incumbent-events "${SOURCE}/replay/events.jsonl" \
  --config "${B}/job_batch02_exact_regime_calibration_v1_20260731T0332/remote_config.json" \
  --capture "${B}/job_batch02_exact_regime_calibration_v1_20260731T0332/baseline_plan_capture.json" \
  --incumbent-point-result "${B}/formal_points/point_0333/point_result.json" \
  --code-root "${B}/gradient_debug_round7_fresh_exclusion_v1/code" \
  --exclude "${B}/job_score_span_repeat_validation_v35_20260731T1940/package/top5.json" \
  --exclude "${T}/package/top5_promoted_formal.json" \
  --exclude "${T}/diagnostic_batch002/top5.json" \
  --exclude "${T}/diagnostic_validation_batch003/top5.json" \
  --exclude "${T}/diagnostic_validation_batch004/top5.json" \
  --exclude "${T}/formal_batch002_promoted_validation004/package/top5_promoted_formal.json" \
  --exclude "${T}/autonomous_v43/diagnostic_probe003/top5.json" \
  --exclude "${SOURCE}/package/top5_promoted_formal.json" \
  --mode diagnostic \
  --force-probe-discovery \
  --output "${TARGET}/top5.second.json"
cmp "${SELECTION}" "${TARGET}/top5.second.json"

jq -e '
  .diagnostic_only == true and
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
' "${TARGET}/replay/summary.json" >/dev/null

jq -n \
  --arg source_summary_sha "$(sha256sum "${SOURCE}/replay/summary.json" | awk '{print $1}')" \
  --arg source_events_sha "$(sha256sum "${SOURCE}/replay/events.jsonl" | awk '{print $1}')" \
  --arg selection_sha "$(sha256sum "${SELECTION}" | awk '{print $1}')" \
  --arg replay_summary_sha "$(sha256sum "${TARGET}/replay/summary.json" | awk '{print $1}')" \
  '{
    status: "clean5_state_neutral",
    source_summary_sha256: $source_summary_sha,
    source_events_sha256: $source_events_sha,
    selection_sha256: $selection_sha,
    replay_summary_sha256: $replay_summary_sha,
    physical_calls: 5,
    valid_replays: 5,
    technical_failures: 0,
    accepted_added: 0
  }' > "${TARGET}/completion_audit.json"
