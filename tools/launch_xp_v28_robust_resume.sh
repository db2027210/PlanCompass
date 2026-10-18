#!/usr/bin/env bash
set -euo pipefail

BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v28_robust_history_unstable_rejection
CAMPAIGN=/home/anonymous/program/resource_adaptive/history/xp_final_algorithm_fresh10_v26_20260731T1935
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
BASE=${EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
FORMAL=${BASE}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245
CANONICAL=${BASE}/formal10_canonical_after_unreserved_call_20260729T1448
RESTORE=${EXPERIENCE}/restore_with_physical_io_reapply_attempt25.sh

EXPECTED_BUNDLE_MANIFEST_SHA256=a632143e9127444e578bf6dacdfb11afa7a03dc8bfb6d55271a6bda62dc88d5a
EXPECTED_RESTORE_SHA256=c0bd2e260ed0f92eb8181657c21184e0c37a6ed1f16c72411b26de69febe1787
: "${EXPECTED_STATE_SHA256:?set EXPECTED_STATE_SHA256 to the verified post-cont053 state SHA}"
: "${HANDOVER_AUDIT:?set HANDOVER_AUDIT to the applied v28 boundary audit path}"
: "${HANDOVER_AUDIT_SHA256:?set HANDOVER_AUDIT_SHA256 to the verified audit SHA}"
: "${OLD_CONTROLLER_PID:?set OLD_CONTROLLER_PID to the stopped v25 controller PID}"
[[ "${OLD_CONTROLLER_PID}" =~ ^[0-9]+$ ]]

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = "${EXPECTED_BUNDLE_MANIFEST_SHA256}"
test "$(sha256sum "${RESTORE}" | awk '{print $1}')" = "${EXPECTED_RESTORE_SHA256}"
/home/anonymous/miniconda3/envs/py310/bin/python3 \
    /home/anonymous/program/resource_adaptive/tools/verify_relative_sha256_manifest.py \
    --root "${BUNDLE}" \
    --manifest "${BUNDLE}/bundle_manifest.json"

test "$(sha256sum "${CAMPAIGN}/state.json" | awk '{print $1}')" = "${EXPECTED_STATE_SHA256}"
test "$(sha256sum "${HANDOVER_AUDIT}" | awk '{print $1}')" = "${HANDOVER_AUDIT_SHA256}"
if [[ -e "/proc/${OLD_CONTROLLER_PID}/stat" ]]; then
    IFS= read -r old_controller_stat < "/proc/${OLD_CONTROLLER_PID}/stat"
    old_controller_tail=${old_controller_stat##*) }
    old_controller_state=${old_controller_tail%% *}
    [[ "${old_controller_state}" == "Z" || "${old_controller_state}" == "X" ]]
    [[ ! -s "/proc/${OLD_CONTROLLER_PID}/cmdline" ]]
    old_controller_children=$(<"/proc/${OLD_CONTROLLER_PID}/task/${OLD_CONTROLLER_PID}/children")
    [[ -z "${old_controller_children}" ]]
fi
test ! -e "${CAMPAIGN}/batches/cont054"

jq -e --arg state_sha "${EXPECTED_STATE_SHA256}" --arg manifest_sha "${EXPECTED_BUNDLE_MANIFEST_SHA256}" '
    .status == "applied_and_verified" and
    .state_sha256 == $state_sha and
    .v28_bundle_manifest_sha256 == $manifest_sha and
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

if [[ "${1:-}" == "--preflight-only" ]]; then
    echo "XP v28 launch preflight passed"
    exit 0
fi
[[ "$#" -eq 0 ]]

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
    --restore-script "${RESTORE}"
