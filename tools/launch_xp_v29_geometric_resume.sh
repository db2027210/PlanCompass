#!/usr/bin/env bash
set -euo pipefail

BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v29_geometric_gradient_escape
CAMPAIGN=/home/anonymous/program/resource_adaptive/history/xp_final_algorithm_fresh10_v26_20260731T1935
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
BASE=${EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
FORMAL=${BASE}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245
CANONICAL=${BASE}/formal10_canonical_after_unreserved_call_20260729T1448
RESTORE=${EXPERIENCE}/restore_with_physical_io_reapply_attempt25.sh

EXPECTED_BUNDLE_MANIFEST_SHA256=32b76ac889a5faa7d4aa1fd254068690cdf1c12d5e503069d7b51c92407e139e
EXPECTED_RESTORE_SHA256=c0bd2e260ed0f92eb8181657c21184e0c37a6ed1f16c72411b26de69febe1787
EXPECTED_CONTROLLER_SHA256=4200511566ae147040396f4fec292ec863c7305b47a68dd49c42d675d6fa7831
: "${EXPECTED_STATE_SHA256:?set EXPECTED_STATE_SHA256 to the verified post-v29-recovery state SHA}"
: "${HANDOVER_AUDIT:?set HANDOVER_AUDIT to the applied v29 recovery audit path}"
: "${HANDOVER_AUDIT_SHA256:?set HANDOVER_AUDIT_SHA256 to the verified audit SHA}"
: "${OLD_CONTROLLER_PID:?set OLD_CONTROLLER_PID to the inactive v28 controller PID}"
[[ "${OLD_CONTROLLER_PID}" =~ ^[0-9]+$ ]]
test "${EXPECTED_STATE_SHA256}" = 2aec45cb53d3d144597a1a59f4786cdd4833db1d262c46592ba045e5ab37a433

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = "${EXPECTED_BUNDLE_MANIFEST_SHA256}"
test "$(sha256sum "${RESTORE}" | awk '{print $1}')" = "${EXPECTED_RESTORE_SHA256}"
test "$(sha256sum /home/anonymous/program/resource_adaptive/tools/resume_xp_fresh0_fixed_contract_target10_v28.py | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA256}"
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
    .post_state_sha256 == $state_sha and
    .projected_post_state_sha256 == $state_sha and
    .bundle_manifest_sha256 == $manifest_sha and
    .pre_state_sha256 == "d14e50a3a67051752e9857fb2c65a2d569f203059c346da67df2dfb3d57795cb" and
    .accepted_count == 4 and
    .accepted_target == 10 and
    .next_batch_index == 54 and
    .incumbent_score == -49.201616429624124 and
    .cont054_file_count == 66 and
    .cont054_subset_jsonl_count == 63 and
    .physical_workload_calls_added == 0 and
    .accepted_incumbent_range_gradient_mutated == false and
    .state_fields_changed == ["status", "last_error_removed"]
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
    echo "XP v29 launch preflight passed"
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
