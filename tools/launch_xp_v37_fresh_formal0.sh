#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v37_20260803
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
CONTROLLER=${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v37.py
PATCH=${PROJECT}/tools/xp_v37_robust_state_patch.py
HANDOFF=${PROJECT}/tools/handoff_xp_v36_to_v37_zero_call.py

test "$(sha256sum "${CONTROLLER}" | awk '{print $1}')" = 4fa3ebe5a669bac3976af6e2815e2b7a12ac40cdb709cd325ba9583cea5fdb13
test "$(sha256sum "${PATCH}" | awk '{print $1}')" = af1fc3bc9aa4bc2b508097dbaa3384f2ee890fb29627410a587c03e9016ecddf
test "$(sha256sum "${HANDOFF}" | awk '{print $1}')" = a7d8d813a854b03a4dc640322752fdb49af5be5e15173d4e4f2eb0c9c3e13b60
test "$(sha256sum "${BUNDLE}/xp_unattended_adapters.py" | awk '{print $1}')" = 7db2d54adba6aad1191970dda51a154937a761caff8c9ce152608985e54e510a
test "$(sha256sum "${BUNDLE}/xp_unattended_continuation.py" | awk '{print $1}')" = 6b7b7d3364c290af2ec65b672ac61c96b1308a8d8f78d0b6b9668ce3e58c0604

active_xp=false
while read -r pid; do
    [[ -z "${pid}" ]] && continue
    stat="$(ps -o stat= -p "${pid}" | tr -d ' ')"
    if [[ -n "${stat}" && "${stat}" != Z* ]]; then
        active_xp=true
        break
    fi
done < <(
    pgrep -f \
        '[r]esume_xp_fresh0_fixed_contract_target10|[x]p_round9_serial_replay|[x]p_legacy_replay_driver' \
        || true
)
if [[ "${active_xp}" == true ]]; then
    echo "active XP controller/replay detected; refusing overlap" >&2
    exit 73
fi
if [[ ! -f "${CAMPAIGN}/state.json" ]]; then
    /home/anonymous/miniconda3/envs/py310/bin/python3 -u "${HANDOFF}"
fi
jq -e '
    .status == "initialized_fresh0_runtime_anchor_ready" and
    .accepted_count == 0 and .accepted_target == 10 and
    .next_batch_index == 2 and .last_batch.batch_id == "cont001" and
    .last_batch.accepted == 0 and .last_batch.technical_failures == 0 and
    .gradient_state.update_count == 0 and
    .v37_zero_call_diagnostic_handoff.workload_calls_added == 0 and
    .v37_zero_call_diagnostic_handoff.formal_state_mutations == 0 and
    .v37_zero_call_diagnostic_handoff.candidate_injection == false
' "${CAMPAIGN}/state.json" >/dev/null

if [[ "${1:-}" == "--preflight-only" ]]; then
    echo "XP v37 fresh-formal0 robust-state preflight passed"
    exit 0
fi
[[ "$#" -eq 0 ]]

exec taskset -c 32-51,288-307 \
    /home/anonymous/miniconda3/envs/py310/bin/python3 -u \
    "${CONTROLLER}" \
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
    --restore-script "${EXPERIENCE}/restore_with_physical_io_reapply_attempt25.sh" \
    --resume-existing
