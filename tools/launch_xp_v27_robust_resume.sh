#!/usr/bin/env bash
set -euo pipefail

BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v27_robust_exact_regime_history
CAMPAIGN=/home/anonymous/program/resource_adaptive/history/xp_final_algorithm_fresh10_v26_20260731T1935
BASE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726
FORMAL=${BASE}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245
CANONICAL=${BASE}/formal10_canonical_after_unreserved_call_20260729T1448

EXPECTED_BUNDLE_MANIFEST_SHA256=8de8a6018d9ff7079ba878e1eb747b84c79319f181952bbf76334eb75b58afcb
: "${EXPECTED_STATE_SHA256:?set EXPECTED_STATE_SHA256 to the verified post-cont053 state SHA}"
: "${HANDOVER_AUDIT:?set HANDOVER_AUDIT to the applied v27 boundary audit path}"
: "${HANDOVER_AUDIT_SHA256:?set HANDOVER_AUDIT_SHA256 to the verified audit SHA}"
: "${OLD_CONTROLLER_PID:?set OLD_CONTROLLER_PID to the stopped v25 controller PID}"

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = "${EXPECTED_BUNDLE_MANIFEST_SHA256}"
/home/anonymous/miniconda3/envs/py310/bin/python3 \
    /home/anonymous/program/resource_adaptive/tools/verify_relative_sha256_manifest.py \
    --root "${BUNDLE}" \
    --manifest "${BUNDLE}/bundle_manifest.json"

test "$(sha256sum "${CAMPAIGN}/state.json" | awk '{print $1}')" = "${EXPECTED_STATE_SHA256}"
test "$(sha256sum "${HANDOVER_AUDIT}" | awk '{print $1}')" = "${HANDOVER_AUDIT_SHA256}"
test ! -e "/proc/${OLD_CONTROLLER_PID}"
test ! -e "${CAMPAIGN}/batches/cont054"

jq -e --arg state_sha "${EXPECTED_STATE_SHA256}" --arg manifest_sha "${EXPECTED_BUNDLE_MANIFEST_SHA256}" '
    .status == "applied_and_verified" and
    .state_sha256 == $state_sha and
    .v27_bundle_manifest_sha256 == $manifest_sha and
    .accepted_count == 4 and
    .accepted_target == 10 and
    .cont053_physical_calls == 5 and
    .cont053_valid_replays >= 0 and
    .cont053_technical_failures >= 0 and
    (.cont053_valid_replays + .cont053_technical_failures) == 5 and
    .cont053_fingerprints_committed_and_excluded == true and
    .physical_workload_calls_added == 0 and
    .formal_state_fields_mutated == false
' "${HANDOVER_AUDIT}" >/dev/null

jq -e '
    .accepted_count == 4 and
    .accepted_target == 10 and
    .next_batch_index == 54 and
    .status == "running" and
    (.last_error | not) and
    .incumbent_score == -49.201616429624124 and
    .last_batch.batch_id == "cont053" and
    .last_batch.mode == "diagnostic_validation" and
    .last_batch.status == "complete" and
    .last_batch.physical_calls == 5 and
    .last_batch.accepted == 0 and
    .last_batch.technical_failures >= 0 and
    .last_batch.technical_failures <= 5 and
    .last_batch.rejected == 0
' "${CAMPAIGN}/state.json" >/dev/null

exec taskset -c 32-51,288-307 \
    /home/anonymous/miniconda3/envs/py310/bin/python3 -u \
    /home/anonymous/program/resource_adaptive/tools/resume_xp_fresh0_fixed_contract_target10_v28.py \
    --resume-existing \
    --bundle-root "${BUNDLE}" \
    --selection /home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/selection.json \
    --replay /home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/replay_summary.json \
    --training-state /home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731/state.json \
    --campaign-root "${CAMPAIGN}" \
    --formal-manifest "${FORMAL}/formal_manifest.json" \
    --authority-counter "${FORMAL}/state/formal_search_accepted_counter.txt" \
    --authority-history "${FORMAL}/optimizer_history_2026-07-29_225003.jsonl" \
    --canonical-state "${CANONICAL}/canonical_state.json" \
    --canonical-manifest "${CANONICAL}/manifest.json" \
    --acceptance-manifest "${BASE}/formal10_cont1_accepted4_cap5_feedback_v1_20260729T1729/formal_manifest.json" \
    --query-snapshot "${BUNDLE}/authority_refresh_accepted4_v5_20260731/query_snapshot_v3.json" \
    --restore-script "${BASE}/restore_with_physical_io_reapply_attempt25.sh"
