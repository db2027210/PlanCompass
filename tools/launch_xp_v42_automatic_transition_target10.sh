#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v38_20260803
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
CONTROLLER=${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v42.py
V42_PATCH=${PROJECT}/tools/xp_v42_automatic_transition_family_patch.py
RECOVERY=${PROJECT}/tools/recover_xp_v42_cont006_transition_zero_call.py
PREFLIGHT=${PROJECT}/tools/preflight_xp_v42_cont006_transition.py
TEST=${PROJECT}/tests/test_xp_v42_automatic_transition_family.py
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3

test "$(sha256sum "${CONTROLLER}" | awk '{print $1}')" = 96e5afdb03f5baa1da444cf8b54eb9d4bbabe85e768b058d3295b6b7cfbf4875
test "$(sha256sum "${V42_PATCH}" | awk '{print $1}')" = 06e6f25175bf572bc2683c2cee539b018035a0a23f0766ac64b3923f512c63ce
test "$(sha256sum "${RECOVERY}" | awk '{print $1}')" = d197dd81eb069f53ac28e0400d72e25884c523673c9bb8db04451c9bfd6a1892
test "$(sha256sum "${PREFLIGHT}" | awk '{print $1}')" = 6001ec58c093284a7b39def97c7349ec0d207832df7d299d2ea13a180db630b0
test "$(sha256sum "${TEST}" | awk '{print $1}')" = 6e7d8a18736e00e832775684623522d3ccb23cd5d8cd96e71329a94b2ac86382
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
if [[ "${state_sha}" = dee1c2a508980bbd105c006ef3d4943b5c577290a8f2ad01218446e39f2313db ]]; then
    "${PYTHON}" -u "${PREFLIGHT}"
    if [[ "${1:-}" == "--preflight-only" ]]; then
        echo "XP v42 source preflight passed; no state change or workload"
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
        .xp_v42_cont006_transition_recovery.contract ==
            "xp_v42_cont006_automatic_transition_zero_call_recovery_v1" and
        .xp_v42_cont006_transition_recovery.workload_calls_added == 0 and
        .xp_v42_cont006_transition_recovery.explain_calls_repeated == 0
    ' "${CAMPAIGN}/state.json" >/dev/null
    if [[ "${1:-}" == "--preflight-only" ]]; then
        "${PYTHON}" -u "${PREFLIGHT}" --post-recovery
        echo "XP v42 post-recovery preflight passed; no workload"
        exit 0
    fi
fi

"${PYTHON}" -u "${PREFLIGHT}" --post-recovery
if [[ "${1:-}" == "--recover-only" ]]; then
    echo "XP v42 zero-call recovery completed; workload not started"
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
