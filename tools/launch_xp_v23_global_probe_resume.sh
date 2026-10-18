#!/usr/bin/env bash
set -euo pipefail

BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v23_global_probe_fallback
CAMPAIGN=/home/anonymous/program/resource_adaptive/history/xp_final_algorithm_fresh10_v26_20260731T1935
BASE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726
FORMAL=${BASE}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245
CANONICAL=${BASE}/formal10_canonical_after_unreserved_call_20260729T1448
EXPECTED_STATE_SHA256=8cebbf27a96a839ad88e313c41403988442922b1ba750a7d17b301a13faab2a9
EXPECTED_MANIFEST_SHA256=a49c5761b210971f93d3b912641e0356264120ea2986c53d70a5ff84cab06ffd

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = "${EXPECTED_MANIFEST_SHA256}"
/home/anonymous/miniconda3/envs/py310/bin/python3 \
    /home/anonymous/program/resource_adaptive/tools/verify_relative_sha256_manifest.py \
    --root "${BUNDLE}" \
    --manifest "${BUNDLE}/bundle_manifest.json"
test "$(sha256sum "${CAMPAIGN}/state.json" | awk '{print $1}')" = "${EXPECTED_STATE_SHA256}"
test ! -e "${CAMPAIGN}/batches/cont026"
test -f "${CAMPAIGN}/quarantine_cont026_v22_offline_diversity_handoff_20260801T2030+0800/handoff_audit.json"

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
