#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v38_20260803
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3
CONTROLLER=${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v51.py
PATCH=${PROJECT}/tools/xp_v51_formal_promotion_routing_patch.py
TEST=${PROJECT}/tools/test_xp_v51_formal_promotion_routing_patch.py
STATE_SHA=16c0bb0512b296c134bf414c77ba43657e627c6d14bdf8f60cde43935720dfb3

require_sha() {
    local path=$1
    local expected=$2
    test -f "${path}"
    test ! -L "${path}"
    test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${CONTROLLER}" edc4c726f268ea881241777dba8f64545b3ca897379f7e1dc22add9b2e26b6c0
require_sha "${PATCH}" 7b9dbfee3a4ecdda0fd3e6463f435d5dda4269adb7906b96e40d4885a43fa2d7
require_sha "${TEST}" c656381f78774888db2cd890bd50ba512a60ad0caa26766764c778d1f27df3a5
require_sha "${PROJECT}/tools/xp_v39_exact_point_pending_trust_patch.py" d5b793ddbaa8eea6fb17519570f83e2e3bac5bac7a20d9a71462e5b35bbbdf83
require_sha "${PROJECT}/tools/xp_v37_robust_state_patch.py" af1fc3bc9aa4bc2b508097dbaa3384f2ee890fb29627410a587c03e9016ecddf
require_sha "${PROJECT}/tools/xp_v50_safe_pool_pending_permutation_patch.py" 5885147540b2181696b5b6ce9baf00c9d57290ce0e02dbd3df40decc2011ce47
require_sha "${BUNDLE}/xp_unattended_adapters.py" 7db2d54adba6aad1191970dda51a154937a761caff8c9ce152608985e54e510a
require_sha "${BUNDLE}/xp_unattended_continuation.py" 6b7b7d3364c290af2ec65b672ac61c96b1308a8d8f78d0b6b9668ce3e58c0604
require_sha "${CAMPAIGN}/state.json" "${STATE_SHA}"
require_sha "${CAMPAIGN}/batches/cont012/score_metadata_normalization/selection.json" 2ae7ad9cac609b03c527a38d14bc62d044b23ee354105b53cd51c690e90a1dd8
require_sha "${CAMPAIGN}/batches/cont012/score_metadata_normalization/audit.json" 1f483f8a6c9d1abc9e328bed03cb53165e084db3c30f5ec3d86651b6c4e105ff
require_sha "${CAMPAIGN}/batches/cont012/preformal_robust_handoff.json" f26a4a3df79cdc8230223a0bbffc439d8a3c9603ffb34ed7ccc77ba061f9cff8

mapfile -t cont012_files < <(
    find "${CAMPAIGN}/batches/cont012" -type f -printf '%P\n' | sort
)
expected_files=(
    preformal_robust_handoff.json
    score_metadata_normalization/audit.json
    score_metadata_normalization/selection.json
)
test "${#cont012_files[@]}" -eq 3
test "${cont012_files[*]}" = "${expected_files[*]}"
test -z "$(find "${CAMPAIGN}/batches/cont012" -type l -print -quit)"

jq -e '
    .status == "automatic_reexploration" and
    .last_error == null and .accepted_count == 0 and .accepted_target == 10 and
    .next_batch_index == 12 and .last_batch.batch_id == "cont011" and
    .last_batch.physical_calls == 5 and .last_batch.technical_failures == 0 and
    .active_phase.batch_id == "cont012" and
    .active_phase.stage == "offline_reexploration" and
    .active_phase.physical_workload_calls == 0 and
    .v51_zero_call_recovery.source_diagnostic_batches == 2 and
    .v51_zero_call_recovery.source_diagnostic_physical_calls == 10 and
    .v51_zero_call_recovery.physical_workload_calls_added == 0 and
    .v51_zero_call_recovery.formal_state_mutation == false and
    .v51_zero_call_recovery.manual_candidate_injection == false and
    .v51_zero_call_recovery.threshold_relaxation == false
' "${CAMPAIGN}/state.json" >/dev/null

PYTHONPATH="${PROJECT}" /home/anonymous/miniconda3/envs/py313/bin/python -m pytest -q \
    "${TEST}" "${PROJECT}/tools/test_xp_v50_safe_pool_pending_permutation_patch.py"

while read -r pid stat; do
    [[ -z "${pid}" ]] && continue
    if [[ "${stat}" != Z* ]]; then
        echo "active XP controller/replay detected: ${pid} ${stat}" >&2
        exit 73
    fi
done < <(
    ps -eo pid=,stat=,comm=,args= | awk '
        $3 ~ /^python/ &&
        $0 ~ /(resume_xp_fresh0_fixed_contract_target10|xp_round9_serial_replay|xp_round9_point_adapter|xp_legacy_replay_driver)/ {
            print $1, $2
        }
    '
)

if [[ "${1:-}" == "--preflight-only" ]]; then
    [[ "$#" -eq 1 ]]
    echo "XP v51 cont012 formal-routing preflight passed"
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
