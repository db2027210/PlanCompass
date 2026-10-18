#!/usr/bin/env bash
set -euo pipefail

BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v25_repeat_collision_reexplore
CAMPAIGN=/home/anonymous/program/resource_adaptive/history/xp_final_algorithm_fresh10_v26_20260731T1935
BASE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726
FORMAL=${BASE}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245
CANONICAL=${BASE}/formal10_canonical_after_unreserved_call_20260729T1448
QUARANTINE=${CAMPAIGN}/batches/quarantine_cont051_repeat_collision_20260803T0030

EXPECTED_BUNDLE_MANIFEST_SHA256=5d10fedfa096b1fb36c70db7c02ec51d20fd98d07812dbf6657131113e98a3f7
EXPECTED_STATE_SHA256=ec48a19c3e314b139b4a40f40d42333bcdc7edabe4f438cb180b0bf45497fc41
EXPECTED_CONT050_SELECTION_SHA256=64f7bac2b86824136b6a56fbc1a37092e6335471edd8b0a8d0c3bf39ac140b3a
EXPECTED_CONT050_REPLAY_SHA256=0441f6ac042aae2eabb635d2b7f2211ab403a3e2b3b69757a74da118c573679f

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = "${EXPECTED_BUNDLE_MANIFEST_SHA256}"
/home/anonymous/miniconda3/envs/py310/bin/python3 \
    /home/anonymous/program/resource_adaptive/tools/verify_relative_sha256_manifest.py \
    --root "${BUNDLE}" \
    --manifest "${BUNDLE}/bundle_manifest.json"

test "$(sha256sum "${CAMPAIGN}/state.json" | awk '{print $1}')" = "${EXPECTED_STATE_SHA256}"
test "$(sha256sum "${CAMPAIGN}/batches/cont050/selection.json" | awk '{print $1}')" = "${EXPECTED_CONT050_SELECTION_SHA256}"
test "$(sha256sum "${CAMPAIGN}/batches/cont050/replay_summary.json" | awk '{print $1}')" = "${EXPECTED_CONT050_REPLAY_SHA256}"
test -d "${QUARANTINE}"
test ! -e "${CAMPAIGN}/batches/cont051"

jq -e '
    .accepted_count == 4 and
    .accepted_target == 10 and
    .next_batch_index == 51 and
    .status == "running" and
    (.last_error | not) and
    .last_batch.batch_id == "cont050" and
    .last_batch.mode == "diagnostic_validation" and
    .last_batch.physical_calls == 5 and
    .last_batch.technical_failures == 0
' "${CAMPAIGN}/state.json" >/dev/null

jq -e '
    .diagnostic_only == true and
    .physical_workload_invocation_count == 5 and
    .successful_real_replay_count == 5 and
    .technical_failure_count == 0 and
    .baseline_restore_verified == true and
    .accepted_count_delta == 0 and
    .accepted_update_applied == false and
    .gradient_update_applied == false and
    .incumbent_updated == false and
    .range_updated == false and
    .state_mutations_applied_by_this_bundle == false
' "${CAMPAIGN}/batches/cont050/replay_summary.json" >/dev/null

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
    --restore-script /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/restore_with_physical_io_reapply_attempt25.sh
