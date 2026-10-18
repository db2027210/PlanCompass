#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v38_20260803
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
CONTROLLER=${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v39.py
PENDING_PATCH=${PROJECT}/tools/xp_v39_exact_point_pending_trust_patch.py
PHASE_PATCH=${PROJECT}/tools/xp_v38_phase_state_patch.py
ROBUST_PATCH=${PROJECT}/tools/xp_v37_robust_state_patch.py
PREFLIGHT=${PROJECT}/tools/preflight_xp_v39_pending_trust.py

test "$(sha256sum "${CONTROLLER}" | awk '{print $1}')" = 326147dd7b795f4d8bad3e912dbf8587de85f6969e95351595ac2fca83c40374
test "$(sha256sum "${PENDING_PATCH}" | awk '{print $1}')" = d5b793ddbaa8eea6fb17519570f83e2e3bac5bac7a20d9a71462e5b35bbbdf83
test "$(sha256sum "${PHASE_PATCH}" | awk '{print $1}')" = 984c2c90117d7dbd777b97db84d499e96c4796ca49231693ec27218c4bb08f5f
test "$(sha256sum "${ROBUST_PATCH}" | awk '{print $1}')" = af1fc3bc9aa4bc2b508097dbaa3384f2ee890fb29627410a587c03e9016ecddf
test "$(sha256sum "${PREFLIGHT}" | awk '{print $1}')" = a977b7146a6fa044e50fcf649b5f7d8924b0271dba9151c9308b6140497c4f2c
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
done < <(pgrep -f 'resume_xp_fresh0_fixed_contract_target10|xp_round9_serial_replay|xp_legacy_replay_driver' || true)
if [[ "${active_xp}" == true ]]; then
    echo "active XP controller/replay detected; refusing overlap" >&2
    exit 73
fi

jq -e '
    .status == "blocked_preworkload" and
    .accepted_count == 0 and .accepted_target == 10 and
    .next_batch_index == 4 and
    .last_batch.batch_id == "cont003" and
    .last_batch.status == "complete" and
    .last_batch.mode == "diagnostic_validation" and
    .last_batch.physical_calls == 5 and
    .last_batch.technical_failures == 0 and
    .last_batch.accepted == 0 and
    .gradient_state.update_count == 0 and
    .active_phase.batch_id == "cont004" and
    .active_phase.stage == "offline_reexploration" and
    .active_phase.physical_workload_calls == 0
' "${CAMPAIGN}/state.json" >/dev/null
test ! -d "${CAMPAIGN}/batches/cont004/serial_replay"

/home/anonymous/miniconda3/envs/py310/bin/python3 -u "${PREFLIGHT}"

if [[ "${1:-}" == "--preflight-only" ]]; then
    echo "XP v39 exact-point pending-trust preflight passed"
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
