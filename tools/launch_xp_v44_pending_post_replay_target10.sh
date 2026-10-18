#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v38_20260803
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
CONTROLLER=${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v44.py
PATCH=${PROJECT}/tools/xp_v44_pending_post_replay_patch.py
PREFLIGHT=${PROJECT}/tools/preflight_xp_v44_pending_post_replay_resume.py
TEST=${PROJECT}/tests/test_xp_v44_pending_post_replay.py
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3

require_sha() {
    local path=$1
    local expected=$2
    test -f "${path}"
    test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${CONTROLLER}" 69c64a8a39187e0b12ba02aa6c394b3a9b4fc401f3ab212ed6537d4eb13aeed8
require_sha "${PATCH}" aa777cf6241b9240f6d4bcd74f28889a5daa98be25d4de01fc35a2eaed6d6385
require_sha "${PREFLIGHT}" 26aa8ade44f368d9afd821a867f7a45d01263d71683d37b3dcf6290ad22b084f
require_sha "${TEST}" ae1e805fa32bc24d75fd7fbfc433594c23b0249860b9881bbc2b5fd602446e0d
require_sha "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v36.py" 5a2617884b0cce051480644548f90e673ad023e894b2ad563d9871ee1aecb90c
require_sha "${PROJECT}/tools/xp_v37_robust_state_patch.py" af1fc3bc9aa4bc2b508097dbaa3384f2ee890fb29627410a587c03e9016ecddf
require_sha "${PROJECT}/tools/xp_v38_phase_state_patch.py" 984c2c90117d7dbd777b97db84d499e96c4796ca49231693ec27218c4bb08f5f
require_sha "${PROJECT}/tools/xp_v39_exact_point_pending_trust_patch.py" d5b793ddbaa8eea6fb17519570f83e2e3bac5bac7a20d9a71462e5b35bbbdf83
require_sha "${PROJECT}/tools/xp_v40_formal_infeasible_reexploration_patch.py" 34480b3004c16ba235f4507d0935c5b102fa53b006d81f3ab3991b79d9892aec
require_sha "${PROJECT}/tools/xp_v41_excluded_ids_sealed_pool_resume_patch.py" 6e52a3d083b393a7ba14268d9acb8bd61c5d57528cf1a42fb8f67f7670042627
require_sha "${PROJECT}/tools/xp_v42_automatic_transition_family_patch.py" 06e6f25175bf572bc2683c2cee539b018035a0a23f0766ac64b3923f512c63ce
require_sha "${PROJECT}/tools/xp_v43_pending_lineage_gate_patch.py" 7b747757a29dfc1a97783954604dc3b79fda00209f232a24eb382782f8bae67a
require_sha "${BUNDLE}/xp_unattended_adapters.py" 7db2d54adba6aad1191970dda51a154937a761caff8c9ce152608985e54e510a
require_sha "${BUNDLE}/xp_unattended_continuation.py" 6b7b7d3364c290af2ec65b672ac61c96b1308a8d8f78d0b6b9668ce3e58c0604
require_sha "${CAMPAIGN}/state.json" b85def4d2d3837f24a5ac69c102da85aac0b2bdefde754d58d26b037389eff45
require_sha "${CAMPAIGN}/batches/cont007/selection.json" bf0d54962de658b97d80fd0a5f0373c6180902938d17efef74d1f4372ca086ca
require_sha "${CAMPAIGN}/batches/cont007/replay_summary.json" e9931aac7742e76a810f25607ca1bedea36394ae1c80745b43dad0cf1364defc

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

test ! -e "${CAMPAIGN}/batches/cont008/selection.json"
test ! -e "${CAMPAIGN}/batches/cont008/replay_summary.json"
"${PYTHON}" -u "${PREFLIGHT}"
if [[ "${1:-}" == "--preflight-only" ]]; then
    [[ "$#" -eq 1 ]]
    echo "XP v44 zero-call preflight passed; workload not started"
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
