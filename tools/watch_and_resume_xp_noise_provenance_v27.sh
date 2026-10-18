#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v9_noise_provenance
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
ATTEMPT=${EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v26_20260731T1935
STATE=${CAMPAIGN}/state.json
CONT001=${CAMPAIGN}/batches/cont001
OLD_PID=3237546

test "$(sha256sum "${PROJECT}/tools/resume_xp_fresh0_score_span_target10_v27.py" | awk '{print $1}')" = \
    ce3cb0704c3871fcd567e0896fd92ab4c3cfc1e05f635f887cc5f9e9d38999ad
test "$(sha256sum "${BUNDLE}/xp_unattended_adapters.py" | awk '{print $1}')" = \
    ccd53d074f9b4110d57ce7845ffbff461f0cbd67747af2d8c8451f6e752dda1b
test "$(sha256sum "${BUNDLE}/build_xp_exact_plan_repeat_selection.py" | awk '{print $1}')" = \
    dbc1427bd9b8ba1eda4778edeffc7c6ae1bb5e00d7b4bde8c118d32b169dce3f

while kill -0 "${OLD_PID}" 2>/dev/null; do
    sleep 10
done

test "$(jq -r '.status' "${STATE}")" = blocked_contract
jq -e '.last_error | contains("exact-plan repeat builder failed")' \
    "${STATE}" >/dev/null
test "$(jq -r '.accepted_count' "${STATE}")" = 0
test "$(jq -r '.next_batch_index' "${STATE}")" = 2
test "$(jq -r '.last_batch.batch_id' "${STATE}")" = cont001
test "$(jq -r '.last_batch.mode' "${STATE}")" = diagnostic_validation
test "$(jq -r '.last_batch.accepted' "${STATE}")" = 0
test "$(jq -r '.last_batch.technical_failures' "${STATE}")" = 0
jq -e '
    .physical_workload_invocation_count == 5 and
    .successful_real_replay_count == 5 and
    .technical_failure_count == 0 and
    .baseline_restore_verified == true and
    .diagnostic_only == true and
    .accepted_update_applied == false and
    .incumbent_updated == false and
    .range_updated == false and
    .gradient_update_applied == false
' "${CONT001}/replay_summary.json" >/dev/null
test ! -e "${CAMPAIGN}/batches/cont002/serial_replay"
test ! -e "${CAMPAIGN}/batches/cont002/replay_summary.json"

sha256sum "${STATE}" > "${CAMPAIGN}/resume_v27_state_before.sha256"
exec taskset -c 32-51,288-307 \
    /home/anonymous/miniconda3/envs/py310/bin/python3 -u \
    "${PROJECT}/tools/resume_xp_fresh0_score_span_target10_v27.py" \
    --resume-existing \
    --bundle-root "${BUNDLE}" \
    --selection "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/selection.json" \
    --replay "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/replay_summary.json" \
    --training-state "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/state.json" \
    --campaign-root "${CAMPAIGN}" \
    --formal-manifest "${ATTEMPT}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/formal_manifest.json" \
    --authority-counter "${ATTEMPT}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/state/formal_search_accepted_counter.txt" \
    --authority-history "${ATTEMPT}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/optimizer_history_2026-07-29_225003.jsonl" \
    --canonical-state "${ATTEMPT}/formal10_canonical_after_unreserved_call_20260729T1448/canonical_state.json" \
    --canonical-manifest "${ATTEMPT}/formal10_canonical_after_unreserved_call_20260729T1448/manifest.json" \
    --acceptance-manifest "${ATTEMPT}/formal10_cont1_accepted4_cap5_feedback_v1_20260729T1729/formal_manifest.json" \
    --query-snapshot "${BUNDLE}/authority_refresh_accepted4_v5_20260731/query_snapshot_v3.json" \
    --restore-script "${EXPERIENCE}/restore_with_physical_io_reapply_attempt25.sh"
