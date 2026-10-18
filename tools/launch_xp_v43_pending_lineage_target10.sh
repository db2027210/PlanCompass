#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v38_20260803
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
CONTROLLER=${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v43.py
PATCH=${PROJECT}/tools/xp_v43_pending_lineage_gate_patch.py
PREFLIGHT=${PROJECT}/tools/preflight_xp_v43_pending_lineage_resume.py
TEST=${PROJECT}/tests/test_xp_v43_pending_lineage_gate.py
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3

require_sha() {
    local path=$1
    local expected=$2
    test -f "${path}"
    test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${CONTROLLER}" 036db8a591eac544dea495395d89252d9eb9fbf0999d5f4d8cd3f1cd6bd6db9c
require_sha "${PATCH}" 7b747757a29dfc1a97783954604dc3b79fda00209f232a24eb382782f8bae67a
require_sha "${PREFLIGHT}" 33dea5667d16e44aa7a3f082fe4c67bc5199e32ba2904bf54c2f376df7588eaa
require_sha "${TEST}" 6c300ae13d2cb3b440fd622cddc01fbcae2d096833384c8706f62919a23400a4
require_sha "${PROJECT}/tools/xp_v42_automatic_transition_family_patch.py" 06e6f25175bf572bc2683c2cee539b018035a0a23f0766ac64b3923f512c63ce
require_sha "${PROJECT}/tools/xp_v39_exact_point_pending_trust_patch.py" d5b793ddbaa8eea6fb17519570f83e2e3bac5bac7a20d9a71462e5b35bbbdf83
require_sha "${CAMPAIGN}/state.json" 784b35282ec23b712640bd39250ae1ead10f1ef18f3c1f8d82c9ba19e2486dd4
require_sha "${CAMPAIGN}/batches/cont007/exact_plan_repeat/selection.json" 6150844886753676f2c3f864cfa58cb0debeab3f2cd9eb915e2312543c99422f
require_sha "${CAMPAIGN}/batches/cont006/replay_summary.json" 9bafd829462c8b7d9fd8282f6043dddaa76c965e53abeb01aafab29c02b9b601

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

test ! -e "${CAMPAIGN}/batches/cont007/selection.json"
test ! -e "${CAMPAIGN}/batches/cont007/replay_summary.json"
"${PYTHON}" -u "${PREFLIGHT}"
if [[ "${1:-}" == "--preflight-only" ]]; then
    echo "XP v43 zero-call preflight passed; workload not started"
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
