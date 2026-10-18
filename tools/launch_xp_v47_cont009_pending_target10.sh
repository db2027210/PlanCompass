#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v38_20260803
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
CONTROLLER=${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v47.py
PATCH=${PROJECT}/tools/xp_v47_safe_pool_pending_lineage_patch.py
RECOVERY=${PROJECT}/tools/recover_xp_v47_cont009_pending_zero_call.py
TEST=${PROJECT}/tests/test_xp_v47_safe_pool_pending_lineage.py
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3
STATE_BEFORE_SHA=4f08a85264c8cc023fe6acd791a14231928d1cf8f6cad2c9678c6607df1695c3
STATE_AFTER_SHA=bbd3c5f47c5559e8cbc5718bf724c49bd8641bfc8c0e71a1d6080ae3a37cb015
CONT009_SELECTION_SHA=3cdebf753dc073e41cc005b92979b6d04400dde3759a56f0561d9ca16454b735

require_sha() {
    local path=$1
    local expected=$2
    test -f "${path}"
    test ! -L "${path}"
    test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${CONTROLLER}" becdf0ad83a1ae287282f271055585c3a2b206faa356f789bb412491b1c7e76c
require_sha "${PATCH}" 1a398881e0a96a63012c1f5e2bc69ca4bd2e2729479aa3382e967f0b2ad319c7
require_sha "${RECOVERY}" c91d65c3dd623fdd4aeb5db876948ebf7fe056ab29f87394cc17d0d4107547c5
require_sha "${TEST}" 545ce4d492f39838bf94cb25377dca3ba7b70bda159f6de56eb3d8a524eac4df
require_sha "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v46.py" 3efb83efac51bf6187d1ddc6b66705d4ff496842c2650232c96437c0e17747b9
require_sha "${PROJECT}/tools/xp_v37_robust_state_patch.py" af1fc3bc9aa4bc2b508097dbaa3384f2ee890fb29627410a587c03e9016ecddf
require_sha "${PROJECT}/tools/xp_v38_phase_state_patch.py" 984c2c90117d7dbd777b97db84d499e96c4796ca49231693ec27218c4bb08f5f
require_sha "${PROJECT}/tools/xp_v39_exact_point_pending_trust_patch.py" d5b793ddbaa8eea6fb17519570f83e2e3bac5bac7a20d9a71462e5b35bbbdf83
require_sha "${PROJECT}/tools/xp_v40_formal_infeasible_reexploration_patch.py" 34480b3004c16ba235f4507d0935c5b102fa53b006d81f3ab3991b79d9892aec
require_sha "${PROJECT}/tools/xp_v41_excluded_ids_sealed_pool_resume_patch.py" 6e52a3d083b393a7ba14268d9acb8bd61c5d57528cf1a42fb8f67f7670042627
require_sha "${PROJECT}/tools/xp_v42_automatic_transition_family_patch.py" 06e6f25175bf572bc2683c2cee539b018035a0a23f0766ac64b3923f512c63ce
require_sha "${PROJECT}/tools/xp_v43_pending_lineage_gate_patch.py" 7b747757a29dfc1a97783954604dc3b79fda00209f232a24eb382782f8bae67a
require_sha "${PROJECT}/tools/xp_v44_pending_post_replay_patch.py" aa777cf6241b9240f6d4bcd74f28889a5daa98be25d4de01fc35a2eaed6d6385
require_sha "${PROJECT}/tools/xp_v45_recovery_compat_patch.py" d37b3b7180a125ee4b5d4cf3014afc9273741934955cb5cddb18ec88f4e1561c
require_sha "${PROJECT}/tools/xp_v46_safe_novel_pool_selector_patch.py" dae58a981853c0c4bddd9b62935c1164e1d8576617b07c118537ecb279f16616
require_sha "${BUNDLE}/xp_round10_diagnostic_bundle.py" c91fa48a7e79f9a1307b2e3eec4a240809861e08472824aba7b8cfffaf71246e
require_sha "${BUNDLE}/xp_unattended_adapters.py" 7db2d54adba6aad1191970dda51a154937a761caff8c9ce152608985e54e510a
require_sha "${BUNDLE}/xp_unattended_continuation.py" 6b7b7d3364c290af2ec65b672ac61c96b1308a8d8f78d0b6b9668ce3e58c0604
require_sha "${CAMPAIGN}/batches/cont008/selection.json" e176b61449684e34231b0dbcfd8edbc8bd7e05432747f36a8d58ee5653f8ec42
require_sha "${CAMPAIGN}/batches/cont008/replay_summary.json" 1ee8971bb6247db9c49954f71ee4f553a66194c3f5b103676e61f2217f3e4d4e
require_sha "${CAMPAIGN}/batches/cont009/score_metadata_normalization/selection.json" 54861dceb16cd367c3457c2af502ef92e54b9da0a3654f103ac519531a513ea7
require_sha "${CAMPAIGN}/batches/cont009/exact_plan_repeat/selection.json" 5b4d4e5823bc5b9ba87320a71f17a983f879fcf06fb0e53b24a2c04f03e17ea0

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
    echo "active XP controller/replay detected; refusing v47 overlap" >&2
    exit 73
fi

validate_recovered() {
    require_sha "${CAMPAIGN}/state.json" "${STATE_AFTER_SHA}"
    require_sha "${CAMPAIGN}/batches/cont009/selection.json" "${CONT009_SELECTION_SHA}"
    jq -e '
        .accepted_count == 0 and .next_batch_index == 9 and
        .status == "diagnostic_ready_preworkload" and
        .active_phase.batch_id == "cont009" and
        .active_phase.stage == "diagnostic_ready_preworkload" and
        .active_phase.physical_workload_calls == 0 and
        .active_phase.selection_gate.passed == true and
        .active_phase.selection_gate.contract ==
            "xp_v47_v46_safe_pool_to_v39_pending_lineage_gate_v1" and
        .v47_zero_call_recovery.cont008_rerun == false and
        .v47_zero_call_recovery.cont009_only == true and
        .v47_zero_call_recovery.formal_state_mutation == false and
        .v47_zero_call_recovery.physical_workload_calls_added == 0
    ' "${CAMPAIGN}/state.json" >/dev/null
    jq -e '
        .batch_mode == "diagnostic_validation" and
        [.top5[].selection_role] == [
            "trusted_calibrated_rank", "trusted_calibrated_rank",
            "trusted_calibrated_rank", "maximin_uncertain_probe",
            "maximin_uncertain_probe"
        ] and
        [.top5[].pending_trusted] == [true, true, true, false, false] and
        all(.top5[]; .formal_trusted_evidence_eligible == false) and
        all(.top5[]; .used_for_gradient == false) and
        all(.top5[]; .used_for_inner_incumbent_update == false) and
        .v47_safe_pool_pending_lineage.v46_probe5_role_gate_reapplied == false and
        .v47_safe_pool_pending_lineage.v42_transition_audit_required == false
    ' "${CAMPAIGN}/batches/cont009/selection.json" >/dev/null
    test ! -e "${CAMPAIGN}/batches/cont009/replay_summary.json"
    test ! -e "${CAMPAIGN}/batches/cont009/serial_replay"
}

state_sha=$(sha256sum "${CAMPAIGN}/state.json" | awk '{print $1}')
case "${state_sha}" in
    "${STATE_BEFORE_SHA}")
        "${PYTHON}" -u "${RECOVERY}" preflight
        if [[ "${1:-}" == "--preflight-only" ]]; then
            [[ "$#" -eq 1 ]]
            echo "XP v47 cont009 zero-call recovery preflight passed"
            exit 0
        fi
        "${PYTHON}" -u "${RECOVERY}" apply
        ;;
    "${STATE_AFTER_SHA}")
        ;;
    *)
        echo "XP v47 state SHA is neither sealed blocked-before nor recovered-after" >&2
        exit 74
        ;;
esac

validate_recovered
if [[ "${1:-}" == "--preflight-only" || "${1:-}" == "--recover-only" ]]; then
    [[ "$#" -eq 1 ]]
    echo "XP v47 cont009 recovery ready; controller not started"
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
