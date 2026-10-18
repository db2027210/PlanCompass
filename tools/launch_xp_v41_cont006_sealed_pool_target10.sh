#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v38_20260803
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
CONTROLLER=${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v41.py
V41_PATCH=${PROJECT}/tools/xp_v41_excluded_ids_sealed_pool_resume_patch.py
V40_PATCH=${PROJECT}/tools/xp_v40_formal_infeasible_reexploration_patch.py
PENDING_PATCH=${PROJECT}/tools/xp_v39_exact_point_pending_trust_patch.py
PHASE_PATCH=${PROJECT}/tools/xp_v38_phase_state_patch.py
ROBUST_PATCH=${PROJECT}/tools/xp_v37_robust_state_patch.py
PREFLIGHT=${PROJECT}/tools/preflight_xp_v41_cont006_sealed_pool.py
RECOVERY=${PROJECT}/tools/recover_xp_v41_cont006_sealed_pool_zero_call.py
TEST=${PROJECT}/tests/test_xp_v41_excluded_ids_sealed_pool.py
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3

test "$(sha256sum "${CONTROLLER}" | awk '{print $1}')" = 3fdd3e6f7fdab03e574abd4c431c8bb4fac5174f72c0a864a4d1c725730e59f2
test "$(sha256sum "${V41_PATCH}" | awk '{print $1}')" = 6e52a3d083b393a7ba14268d9acb8bd61c5d57528cf1a42fb8f67f7670042627
test "$(sha256sum "${V40_PATCH}" | awk '{print $1}')" = 34480b3004c16ba235f4507d0935c5b102fa53b006d81f3ab3991b79d9892aec
test "$(sha256sum "${PENDING_PATCH}" | awk '{print $1}')" = d5b793ddbaa8eea6fb17519570f83e2e3bac5bac7a20d9a71462e5b35bbbdf83
test "$(sha256sum "${PHASE_PATCH}" | awk '{print $1}')" = 984c2c90117d7dbd777b97db84d499e96c4796ca49231693ec27218c4bb08f5f
test "$(sha256sum "${ROBUST_PATCH}" | awk '{print $1}')" = af1fc3bc9aa4bc2b508097dbaa3384f2ee890fb29627410a587c03e9016ecddf
test "$(sha256sum "${PREFLIGHT}" | awk '{print $1}')" = 33a4f8dda9c6d2f6156ee7d52a0cee86a7a383591180f1bec4942b9c17c23d3f
test "$(sha256sum "${RECOVERY}" | awk '{print $1}')" = c0541d287c5ac3e30b4a4883d4c2c0066147131630b8c393452fa898e7f464ce
test "$(sha256sum "${TEST}" | awk '{print $1}')" = 613bd9b8309d0adf197d1557fa01e3016398a13e1c899a4ef382b0f621c7858f
test "$(sha256sum "${BUNDLE}/xp_unattended_adapters.py" | awk '{print $1}')" = 7db2d54adba6aad1191970dda51a154937a761caff8c9ce152608985e54e510a
test "$(sha256sum "${BUNDLE}/xp_unattended_continuation.py" | awk '{print $1}')" = 6b7b7d3364c290af2ec65b672ac61c96b1308a8d8f78d0b6b9668ce3e58c0604
test "$(sha256sum "${CAMPAIGN}/batches/cont006/explain_only/explain_only_generation.json" | awk '{print $1}')" = aa326f82f2f5727fa5288a1484be6eaa3b9fd2be2b373b94f710e5a4ed747751

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

state_sha="$(sha256sum "${CAMPAIGN}/state.json" | awk '{print $1}')"
if [[ "${state_sha}" = 163200e9dcf0a95c27cf2e73aed066229980c85d574c7bc9b2367c1bdf075e75 ]]; then
    "${PYTHON}" -u "${PREFLIGHT}"
    if [[ "${1:-}" == "--preflight-only" ]]; then
        echo "XP v41 source preflight passed; no state change or workload"
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
        .xp_v41_cont006_sealed_pool_recovery.contract ==
            "xp_v41_cont006_sealed_pool_zero_call_recovery_v1" and
        .xp_v41_cont006_sealed_pool_recovery.workload_calls_added == 0 and
        .xp_v41_cont006_sealed_pool_recovery.explain_calls_repeated == 0
    ' "${CAMPAIGN}/state.json" >/dev/null
    if [[ "${1:-}" == "--preflight-only" ]]; then
        "${PYTHON}" -u "${PREFLIGHT}" --post-recovery
        echo "XP v41 post-recovery preflight passed; no workload"
        exit 0
    fi
fi

"${PYTHON}" -u "${PREFLIGHT}" --post-recovery
if [[ "${1:-}" == "--recover-only" ]]; then
    echo "XP v41 zero-call recovery completed; workload not started"
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
