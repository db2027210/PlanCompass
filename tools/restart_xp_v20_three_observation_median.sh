#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v26_20260731T1935
BATCH=${CAMPAIGN}/batches/cont017
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v20_three_observation_median
OLD_SUMMARY=${BATCH}/replay_summary.json
QUARANTINED_SUMMARY=${BATCH}/replay_summary.old_single_observation_v19.json
STATE_LOCK=${CAMPAIGN}/state.json.lock
CONSUMED_LOCK=${CAMPAIGN}/state.json.lock.v20_handover_consumed
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3

EXPECTED_STATE_SHA=02568483efc695d36986c1792bbd7611257556921b87c7adf2492b95dd0502ab
EXPECTED_SELECTION_SHA=658d0fc8e57f8bf79cdc082ab7b17c7761d4e99443d9943dab031eeafe199689
EXPECTED_ADAPTER_SHA=7101add780b151b2317b35d0f892b199d75a8650868437223b5ccf8dfc42c789

test "$(sha256sum "${CAMPAIGN}/state.json" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
test "$(sha256sum "${BATCH}/selection.json" | awk '{print $1}')" = "${EXPECTED_SELECTION_SHA}"
test "$(sha256sum "${BUNDLE}/xp_unattended_adapters.py" | awk '{print $1}')" = "${EXPECTED_ADAPTER_SHA}"
for rank in 01 02 03 04 05; do
    test -f "${BATCH}/serial_replay/rank_${rank}/result.json"
done

if test -f "${OLD_SUMMARY}"; then
    test -f "${STATE_LOCK}"
    test ! -e "${CONSUMED_LOCK}"
    test ! -e "${QUARANTINED_SUMMARY}"
    test "$(jq -r '.physical_workload_invocation_count' "${OLD_SUMMARY}")" = 5
    test "$(jq -r '.successful_real_replay_count' "${OLD_SUMMARY}")" = 5
    test "$(jq -r '.technical_failure_count' "${OLD_SUMMARY}")" = 0
    test "$(jq -r '.continuation_acceptance_policy' "${OLD_SUMMARY}")" = \
        sequential_trusted_only_actual_score_improvement_v1
    mv "${OLD_SUMMARY}" "${QUARANTINED_SUMMARY}"
    mv "${STATE_LOCK}" "${CONSUMED_LOCK}"
else
    test ! -e "${STATE_LOCK}"
    test -f "${CONSUMED_LOCK}"
    test -f "${QUARANTINED_SUMMARY}"
    test "$(jq -r '.physical_workload_invocation_count' "${QUARANTINED_SUMMARY}")" = 5
    test "$(jq -r '.successful_real_replay_count' "${QUARANTINED_SUMMARY}")" = 5
    test "$(jq -r '.technical_failure_count' "${QUARANTINED_SUMMARY}")" = 0
    test "$(jq -r '.continuation_acceptance_policy' "${QUARANTINED_SUMMARY}")" = \
        sequential_trusted_only_actual_score_improvement_v1
fi

exec "${PYTHON}" -u "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v28.py" \
    --resume-existing \
    --bundle-root "${BUNDLE}" \
    --selection "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/selection.json" \
    --replay "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/replay_summary.json" \
    --training-state "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/state.json" \
    --campaign-root "${CAMPAIGN}" \
    --formal-manifest /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/formal_manifest.json \
    --authority-counter /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/state/formal_search_accepted_counter.txt \
    --authority-history /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/optimizer_history_2026-07-29_225003.jsonl \
    --canonical-state /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_canonical_after_unreserved_call_20260729T1448/canonical_state.json \
    --canonical-manifest /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_canonical_after_unreserved_call_20260729T1448/manifest.json \
    --acceptance-manifest /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont1_accepted4_cap5_feedback_v1_20260729T1729/formal_manifest.json \
    --query-snapshot "${BUNDLE}/authority_refresh_accepted4_v5_20260731/query_snapshot_v3.json" \
    --restore-script /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/restore_with_physical_io_reapply_attempt25.sh
