#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v38_20260803
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
CONTROLLER=${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v45.py
PATCH=${PROJECT}/tools/xp_v45_recovery_compat_patch.py
V44_PATCH=${PROJECT}/tools/xp_v44_pending_post_replay_patch.py
RECOVERY=${PROJECT}/tools/recover_xp_v45_blocked_preworkload_zero_call.py
READY_PREFLIGHT=${PROJECT}/tools/preflight_xp_v45_automatic_reexploration_ready.py
REAL_VALIDATOR=${PROJECT}/tools/validate_xp_v45_real_explain_selection.py
TEST=${PROJECT}/tests/test_xp_v45_real_reexploration_resume.py
VALIDATION_RESULT=${XP_V45_VALIDATION_RESULT:-/tmp/xp_v45_real_selection_validation_20260803/validation_result.json}
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3
STATE_BEFORE_SHA=e39dfc261c799c3b36dc40b8d5c9979401db8d900f9091c49fbc22a50f173c7d
STATE_AFTER_SHA=6682932359b7e5c480d7de14f72cf9b99f1f67d61fbd81cefa729da952de6086

require_sha() {
    local path=$1
    local expected=$2
    test -f "${path}"
    test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${CONTROLLER}" c5759e49118e4e1be8e7f86700f771f3372dcd546760f58b5e13319bd23a5fe9
require_sha "${PATCH}" d37b3b7180a125ee4b5d4cf3014afc9273741934955cb5cddb18ec88f4e1561c
require_sha "${V44_PATCH}" aa777cf6241b9240f6d4bcd74f28889a5daa98be25d4de01fc35a2eaed6d6385
require_sha "${RECOVERY}" 993f27a1ee7b16c390e0dfba514d7b87ade95a2f8a8f71eb2e6a36ad443d7d9c
require_sha "${READY_PREFLIGHT}" 2bf6cab6368028bb5d69aeac0f9df475d28fe71442f952f7a73c8d38d35a3bd7
require_sha "${REAL_VALIDATOR}" f688d3d9e446eaead337f339753279fa62d382dfd7c6a924415e80d6299cd26f
require_sha "${TEST}" a1b9ef78a0ec6e1afd91fd652bf7ccae82305450dbdc26aabd32c78dc7f591a3
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
require_sha "${CAMPAIGN}/batches/cont007/selection.json" bf0d54962de658b97d80fd0a5f0373c6180902938d17efef74d1f4372ca086ca
require_sha "${CAMPAIGN}/batches/cont007/replay_summary.json" e9931aac7742e76a810f25607ca1bedea36394ae1c80745b43dad0cf1364defc
require_sha "${CAMPAIGN}/batches/cont008/v44_pending_post_replay_reroute.json" 134a6c6cf38f80680576ab239c54b3c51f07650aef7dfe6b6dd435d87da3a04a

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

XP_STATE_SHA=$(sha256sum "${CAMPAIGN}/state.json" | awk '{print $1}')
case "${XP_STATE_SHA}" in
    "${STATE_BEFORE_SHA}")
        "${PYTHON}" -u "${RECOVERY}" preflight
        if [[ "${1:-}" == "--preflight-only" ]]; then
            [[ "$#" -eq 1 ]]
            echo "XP v45 pre-recovery zero-call preflight passed"
            exit 0
        fi
        "${PYTHON}" -u "${RECOVERY}" apply
        ;;
    "${STATE_AFTER_SHA}")
        ;;
    *)
        echo "XP v45 state SHA is neither sealed before nor recovered after" >&2
        exit 74
        ;;
esac

"${PYTHON}" -u "${READY_PREFLIGHT}"
if [[ "${1:-}" == "--recover-only" ]]; then
    [[ "$#" -eq 1 ]]
    echo "XP v45 zero-call recovery complete; controller not started"
    exit 0
fi
[[ "$#" -eq 0 ]]

test -f "${VALIDATION_RESULT}"
jq -e '
    .contract == "xp_v45_real_explain_selection_validation_v1" and
    .candidate_count == 5 and
    (.fingerprint_ids | length) == 5 and
    .all_new_noncorner == true and
    .actual_best_top3_gate_bypassed == false and
    .workload_started == false and
    .formal_state_mutation == false
' "${VALIDATION_RESULT}" >/dev/null
VALIDATION_SELECTION=$(jq -r '.selection_path' "${VALIDATION_RESULT}")
VALIDATION_SELECTION_SHA=$(jq -r '.selection_sha256' "${VALIDATION_RESULT}")
[[ "${VALIDATION_SELECTION}" == /* ]]
require_sha "${VALIDATION_SELECTION}" "${VALIDATION_SELECTION_SHA}"

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
