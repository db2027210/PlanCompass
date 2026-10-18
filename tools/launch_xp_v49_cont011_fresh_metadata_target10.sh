#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v38_20260803
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
CONTROLLER=${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v49.py
PATCH=${PROJECT}/tools/xp_v49_fresh_selection_metadata_patch.py
RECOVERY=${PROJECT}/tools/recover_xp_v49_cont011_fresh_metadata_zero_call.py
TEST=${PROJECT}/tools/test_xp_v49_fresh_selection_metadata_patch.py
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3
STATE_BEFORE_SHA=0c464a7e1001dfd72be5b9bc1da597838eed2d6a19215fed2c0c4ce951815846
STATE_AFTER_SHA=b6b05f76323bfc95ce7b53dc044dc524aa84ab0347276182507ba170144d97be

require_sha() {
    local path=$1
    local expected=$2
    test -f "${path}"
    test ! -L "${path}"
    test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${CONTROLLER}" 0b4fc155756dffabedf39038a0e6cfe76c7bd3ef4418e2906b4feb107c669716
require_sha "${PATCH}" 579101cf87df54a86e10dd034300bce06916aa6084c390b13a02c8d2beaf71b7
require_sha "${RECOVERY}" 263f4e35c65f85a97f26657ca0f4b6e4100a5d4b0a9725a3b03872fd9f9a549f
require_sha "${TEST}" 75daff033a9cc457beee47e54190842bbfc5cc31a1158f9c30397117c511430d
require_sha "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v48.py" 8412604054d24e7e98f1066cfc122e32eaca02b387208afe23385803437ed101
require_sha "${BUNDLE}/xp_unattended_adapters.py" 7db2d54adba6aad1191970dda51a154937a761caff8c9ce152608985e54e510a
require_sha "${BUNDLE}/xp_unattended_continuation.py" 6b7b7d3364c290af2ec65b672ac61c96b1308a8d8f78d0b6b9668ce3e58c0604
require_sha "${CAMPAIGN}/batches/cont010/selection.json" 2f79982be496cac7188870d0edfddab58a20ed10962b1721f07a85bffa2a968b
require_sha "${CAMPAIGN}/batches/cont010/replay_summary.json" 902ce053792c34d078d3be966b24e20f918804928544a138e73ec6c52feb9c2f

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
    echo "active XP controller/replay detected; refusing v49 overlap" >&2
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
        .v49_zero_call_recovery.cont010_rerun == false and
        .v49_zero_call_recovery.cont011_only == true and
        .v49_zero_call_recovery.trusted_candidates_promoted_by_recovery == 0 and
        .v49_zero_call_recovery.formal_state_mutation == false and
        .v49_zero_call_recovery.physical_workload_calls_added == 0
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
            echo "XP v49 cont011 zero-call recovery preflight passed"
            exit 0
        fi
        "${PYTHON}" -u "${RECOVERY}" apply
        ;;
    "${STATE_AFTER_SHA}")
        ;;
    *)
        echo "XP v49 state SHA is neither sealed blocked-before nor recovered-after" >&2
        exit 74
        ;;
esac

validate_recovered
if [[ "${1:-}" == "--preflight-only" || "${1:-}" == "--recover-only" ]]; then
    [[ "$#" -eq 1 ]]
    echo "XP v49 cont011 recovery ready; controller not started"
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

