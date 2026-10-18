#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v38_20260803
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
CONTROLLER=${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v40.py
REROUTE_PATCH=${PROJECT}/tools/xp_v40_formal_infeasible_reexploration_patch.py
PENDING_PATCH=${PROJECT}/tools/xp_v39_exact_point_pending_trust_patch.py
PHASE_PATCH=${PROJECT}/tools/xp_v38_phase_state_patch.py
ROBUST_PATCH=${PROJECT}/tools/xp_v37_robust_state_patch.py
PREFLIGHT=${PROJECT}/tools/preflight_xp_v40_infeasible_reexploration.py
RECOVERY=${PROJECT}/tools/recover_xp_v40_infeasible_cont005_zero_call.py
TEST=${PROJECT}/tests/test_xp_v40_infeasible_reexploration.py
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3

test "$(sha256sum "${CONTROLLER}" | awk '{print $1}')" = 759f74ff363caa8d221ed02a0819f3f387a3bcfab4807d240d5027056f68d8b8
test "$(sha256sum "${REROUTE_PATCH}" | awk '{print $1}')" = 34480b3004c16ba235f4507d0935c5b102fa53b006d81f3ab3991b79d9892aec
test "$(sha256sum "${PENDING_PATCH}" | awk '{print $1}')" = d5b793ddbaa8eea6fb17519570f83e2e3bac5bac7a20d9a71462e5b35bbbdf83
test "$(sha256sum "${PHASE_PATCH}" | awk '{print $1}')" = 984c2c90117d7dbd777b97db84d499e96c4796ca49231693ec27218c4bb08f5f
test "$(sha256sum "${ROBUST_PATCH}" | awk '{print $1}')" = af1fc3bc9aa4bc2b508097dbaa3384f2ee890fb29627410a587c03e9016ecddf
test "$(sha256sum "${PREFLIGHT}" | awk '{print $1}')" = 7291f51d66dc234a1a380aac509d9f954f3c74dfd7e3c137dcca52b1de696083
test "$(sha256sum "${RECOVERY}" | awk '{print $1}')" = cf24957b5dffe4da0ba1a5d0462ec6c87aa1159f0411e3dbd0cfde44c4fa9732
test "$(sha256sum "${TEST}" | awk '{print $1}')" = 2eb6130b86e240b8936aca63a2df631b39e0725b8fe70ba84c41fbdd461f0bc7
test "$(sha256sum "${BUNDLE}/xp_unattended_adapters.py" | awk '{print $1}')" = 7db2d54adba6aad1191970dda51a154937a761caff8c9ce152608985e54e510a
test "$(sha256sum "${BUNDLE}/xp_unattended_continuation.py" | awk '{print $1}')" = 6b7b7d3364c290af2ec65b672ac61c96b1308a8d8f78d0b6b9668ce3e58c0604

active_xp=false
while read -r pid stat; do
    [[ -z "${pid}" ]] && continue
    if [[ "${stat}" != Z* ]]; then
        active_xp=true
        break
    fi
done < <(
    ps -eo pid=,stat=,comm=,args= | awk '
        $3 ~ /^python/ &&
        $0 ~ /(resume_xp_fresh0_fixed_contract_target10|xp_round9_serial_replay|xp_legacy_replay_driver)/ {
            print $1, $2
        }
    '
)
if [[ "${active_xp}" == true ]]; then
    echo "active XP controller/replay detected; refusing overlap" >&2
    exit 73
fi

if [[ "$(sha256sum "${CAMPAIGN}/state.json" | awk '{print $1}')" = 669f60e63c4a3a775c33f76208f57ab557cdf2664bf1e26b27405eb2240dc3aa ]]; then
    "${PYTHON}" -u "${PREFLIGHT}"
    if [[ "${1:-}" == "--preflight-only" ]]; then
        echo "XP v40 zero-call source preflight passed"
        exit 0
    fi
    "${PYTHON}" -u "${RECOVERY}" --campaign-root "${CAMPAIGN}"
else
    jq -e '
        .status == "offline_reexploration" and
        .accepted_count == 0 and .accepted_target == 10 and
        .next_batch_index == 6 and
        .last_batch.batch_id == "cont004" and
        .active_phase.batch_id == "cont006" and
        .active_phase.physical_workload_calls == 0 and
        .active_phase.manual_candidate_injection == false and
        .xp_v40_zero_call_infeasible_reexploration_recovery.contract ==
            "xp_v40_cont005_infeasible_zero_call_schedule_recovery_v1" and
        .xp_v40_zero_call_infeasible_reexploration_recovery.workload_calls_added == 0
    ' "${CAMPAIGN}/state.json" >/dev/null
    if [[ "${1:-}" == "--preflight-only" ]]; then
        "${PYTHON}" -u "${PREFLIGHT}" --post-recovery
        echo "XP v40 post-recovery preflight passed"
        exit 0
    fi
fi

"${PYTHON}" -u "${PREFLIGHT}" --post-recovery
if [[ "${1:-}" == "--recover-only" ]]; then
    echo "XP v40 zero-call recovery completed; workload not started"
    exit 0
fi
[[ "$#" -eq 0 ]]

exec taskset -c 32-51,288-307 \
    "${PYTHON}" -u \
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
