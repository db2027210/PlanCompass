#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v35_20260803T1220
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
MANIFEST=${PROJECT}/history/xp_v35_authoritative_r0_manifest_20260803.json
MANIFEST_SHA256=aa4a10b1fb3b2cccdff104978cd5909230e4b41584e949225a7516815159e639

test "$(sha256sum "${MANIFEST}" | awk '{print $1}')" = "${MANIFEST_SHA256}"
jq -e '
    .schema_version == "xp_v35_authoritative_r0_manifest_v1" and
    .accepted_count_initial == 0 and
    .accepted_target == 10 and
    .formal_authority_inheritance == false and
    .restore_reexecution_required == false and
    .v34_partial_campaign_role == "technical_audit_only_not_inherited" and
    .workload_calls_added_by_initialization == 0 and
    .cpuset == "32-51,288-307" and
    .database == "hybench_sf10x"
' "${MANIFEST}" >/dev/null

/home/anonymous/miniconda3/envs/py310/bin/python3 \
    "${PROJECT}/tools/preflight_xp_v35.py"

if [[ "${1:-}" == "--preflight-only" ]]; then
    echo "XP v35 authoritative-round9 R0 launch preflight passed"
    exit 0
fi
[[ "$#" -eq 0 ]]

exec taskset -c 32-51,288-307 \
    /home/anonymous/miniconda3/envs/py310/bin/python3 -u \
    "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v35.py" \
    --bundle-root "${BUNDLE}" \
    --selection "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/selection.json" \
    --replay "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/replay_summary.json" \
    --training-selection "${PROJECT}/history/xp_final_algorithm_fresh10_v32_20260803T1058/batches/cont001/selection.json" \
    --training-replay "${PROJECT}/history/xp_final_algorithm_fresh10_v32_20260803T1058/batches/cont001/replay_summary.json" \
    --prior-training-selection "${PROJECT}/history/xp_final_algorithm_fresh10_v31_20260803T1009/batches/cont001/selection.json" \
    --prior-training-replay "${PROJECT}/history/xp_final_algorithm_fresh10_v31_20260803T1009/batches/cont001/replay_summary.json" \
    --training-state "${BUNDLE}/fresh0_initial_state.json" \
    --historical-accepted-excluded 4 \
    --campaign-root "${CAMPAIGN}" \
    --formal-manifest "${FORMAL}/formal_manifest.json" \
    --authority-counter "${FORMAL}/state/formal_search_accepted_counter.txt" \
    --authority-history "${FORMAL}/optimizer_history_2026-07-29_104036.jsonl" \
    --canonical-state "${BUNDLE}/fresh0_initial_state.json" \
    --canonical-manifest "${BUNDLE}/fresh0_initialization_manifest.json" \
    --acceptance-manifest "${FORMAL}/formal_manifest.json" \
    --query-snapshot "${BUNDLE}/authority_refresh_accepted4_v5_20260731/query_snapshot_v3.json" \
    --restore-script "${EXPERIENCE}/restore_with_physical_io_reapply_attempt25.sh"
