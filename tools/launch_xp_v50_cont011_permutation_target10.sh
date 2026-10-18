#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v38_20260803
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
CONTROLLER=${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v50.py
PATCH=${PROJECT}/tools/xp_v50_safe_pool_pending_permutation_patch.py
RECOVERY=${PROJECT}/tools/recover_xp_v50_cont011_permutation_zero_call.py
TEST=${PROJECT}/tools/test_xp_v50_safe_pool_pending_permutation_patch.py
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3
STATE_BEFORE_SHA=74179e9d8d5f97cdb54e90d8f1da93dbf49dde5c5b4bfe791346fffeac4a65bf
STATE_AFTER_SHA=9ae7494f6c9073d454318b4b66118ae9a6f35a5f5981dbd165c062b7475e921f

require_sha() {
    local path=$1
    local expected=$2
    test -f "${path}"
    test ! -L "${path}"
    test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${CONTROLLER}" 26988c116bec3e6f7dcdff88c6c972198e3fe39e9c0d4a62661c572f2df4d8ad
require_sha "${PATCH}" 5885147540b2181696b5b6ce9baf00c9d57290ce0e02dbd3df40decc2011ce47
require_sha "${RECOVERY}" 6b5098941409141631d84533608c0d56ddb2a5bebb8d5db3cebf2a69d1eeb5e7
require_sha "${TEST}" 8a5592585ecac0e1f56ff9a5714f2a49be91faa310cfc552c16d11e05607d930
require_sha "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v49.py" 0b4fc155756dffabedf39038a0e6cfe76c7bd3ef4418e2906b4feb107c669716
require_sha "${PROJECT}/tools/xp_v49_fresh_selection_metadata_patch.py" 579101cf87df54a86e10dd034300bce06916aa6084c390b13a02c8d2beaf71b7
require_sha "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v48.py" 8412604054d24e7e98f1066cfc122e32eaca02b387208afe23385803437ed101
require_sha "${BUNDLE}/xp_unattended_adapters.py" 7db2d54adba6aad1191970dda51a154937a761caff8c9ce152608985e54e510a
require_sha "${BUNDLE}/xp_unattended_continuation.py" 6b7b7d3364c290af2ec65b672ac61c96b1308a8d8f78d0b6b9668ce3e58c0604
require_sha "${CAMPAIGN}/batches/cont010/selection.json" 2f79982be496cac7188870d0edfddab58a20ed10962b1721f07a85bffa2a968b
require_sha "${CAMPAIGN}/batches/cont010/replay_summary.json" 902ce053792c34d078d3be966b24e20f918804928544a138e73ec6c52feb9c2f

PYTHONPATH="${PROJECT}" "${PYTHON}" -c '
from tools import test_xp_v50_safe_pool_pending_permutation_patch as test
test.test_live_repeat_is_a_valid_nonidentity_permutation()
test.test_duplicate_source_binding_fails_closed()
'

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
        $0 ~ /(resume_xp_fresh0_fixed_contract_target10|xp_round9_serial_replay|xp_round9_point_adapter|xp_legacy_replay_driver)/ {
            print $1, $2
        }
    '
)
if [[ "${active_xp}" == true ]]; then
    echo "active XP controller/replay detected; refusing v50 overlap" >&2
    exit 73
fi

validate_recovered() {
    require_sha "${CAMPAIGN}/state.json" "${STATE_AFTER_SHA}"
    jq -e '
        .accepted_count == 0 and .accepted_target == 10 and
        .next_batch_index == 11 and .status == "automatic_reexploration" and
        .last_error == null and .last_batch.batch_id == "cont010" and
        .last_batch.physical_calls == 5 and
        .last_batch.technical_failures == 0 and
        .active_phase.batch_id == "cont011" and
        .active_phase.stage == "offline_reexploration" and
        .active_phase.physical_workload_calls == 0 and
        .v50_zero_call_recovery.cont010_rerun == false and
        .v50_zero_call_recovery.cont011_only == true and
        .v50_zero_call_recovery.trusted_candidates_promoted_by_recovery == 0 and
        .v50_zero_call_recovery.formal_state_mutation == false and
        .v50_zero_call_recovery.physical_workload_calls_added == 0 and
        .v50_zero_call_recovery.fixed_mapping.one_to_one_permutation_required == true and
        .v50_zero_call_recovery.fixed_mapping.candidate_order_changed == false
    ' "${CAMPAIGN}/state.json" >/dev/null
    test ! -e "${CAMPAIGN}/batches/cont011/selection.json"
    test ! -e "${CAMPAIGN}/batches/cont011/replay_summary.json"
    test ! -e "${CAMPAIGN}/batches/cont011/serial_replay"
}

state_sha=$(sha256sum "${CAMPAIGN}/state.json" | awk '{print $1}')
case "${state_sha}" in
    "${STATE_BEFORE_SHA}")
        "${PYTHON}" -u "${RECOVERY}" preflight
        if [[ "${1:-}" == "--preflight-only" ]]; then
            [[ "$#" -eq 1 ]]
            echo "XP v50 cont011 zero-call recovery preflight passed"
            exit 0
        fi
        "${PYTHON}" -u "${RECOVERY}" apply
        ;;
    "${STATE_AFTER_SHA}")
        ;;
    *)
        echo "XP v50 state SHA is neither sealed blocked-before nor recovered-after" >&2
        exit 74
        ;;
esac

validate_recovered
if [[ "${1:-}" == "--preflight-only" || "${1:-}" == "--recover-only" ]]; then
    [[ "$#" -eq 1 ]]
    echo "XP v50 cont011 recovery ready; controller not started"
    exit 0
fi
[[ "$#" -eq 0 ]]

exec taskset -c 32-51,288-307 \
    "${PYTHON}" -u "${CONTROLLER}" \
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
