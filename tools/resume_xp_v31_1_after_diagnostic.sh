#!/usr/bin/env bash
set -euo pipefail

BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v31_1_metadata_normalize
CAMPAIGN=/home/anonymous/program/resource_adaptive/history/xp_final_algorithm_fresh10_v31_20260803T1009
TRAINING_STATE=${BUNDLE}/fresh0_initial_state.json
SEED_SELECTION=/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/selection.json
SEED_REPLAY=/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/replay_summary.json
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
BASE=${EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
FORMAL=${BASE}/formal10_after_v3g_entry_gate_20260729T1040
CANONICAL_STATE=${BUNDLE}/fresh0_initial_state.json
CANONICAL_MANIFEST=${BUNDLE}/fresh0_initialization_manifest.json
RESTORE=${EXPERIENCE}/restore_with_physical_io_reapply_attempt25.sh

EXPECTED_BUNDLE_MANIFEST_SHA256=cef14b406902b307219d975ef30e9e23a730358407b218f6e7e2de388910b162
EXPECTED_CONTROLLER_SHA256=8b77801459170af404d886398fd065f7b7588a05a2068c202bd6197ba9bf68e2

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = "${EXPECTED_BUNDLE_MANIFEST_SHA256}"
test "$(sha256sum /home/anonymous/program/resource_adaptive/tools/resume_xp_fresh0_fixed_contract_target10_v31.py | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA256}"
/home/anonymous/miniconda3/envs/py310/bin/python3 \
    /home/anonymous/program/resource_adaptive/tools/verify_relative_sha256_manifest.py \
    --root "${BUNDLE}" \
    --manifest "${BUNDLE}/bundle_manifest.json"

# Safe application boundary: the frozen v31 controller must have completed
# its diagnostic batch(es) and failed closed before any formal replay.  Never
# replace or resume an in-flight selection.
jq -e '
    .status == "blocked_contract" and
    .accepted_count == 0 and
    .last_batch.status == "complete" and
    .last_batch.mode == "diagnostic_validation" and
    .last_batch.score_regime == "xp_v31_fresh0_band_target_v1" and
    .last_batch.score_admission_eligible == true and
    .last_batch.historical_scores_used_for_fresh_admission == false and
    (.last_error | contains("formal candidate contains historical score-domain evidence"))
' "${CAMPAIGN}/state.json" >/dev/null

NEXT_INDEX=$(jq -r '.next_batch_index' "${CAMPAIGN}/state.json")
NEXT_BATCH=$(printf 'cont%03d' "${NEXT_INDEX}")
test ! -e "${CAMPAIGN}/batches/${NEXT_BATCH}/selection.json"
test ! -e "${CAMPAIGN}/batches/${NEXT_BATCH}/replay_summary.json"

if [[ "${1:-}" == "--preflight-only" ]]; then
    echo "XP v31.1 metadata-normalization resume preflight passed"
    exit 0
fi
[[ "$#" -eq 0 ]]

exec taskset -c 32-51,288-307 \
    /home/anonymous/miniconda3/envs/py310/bin/python3 -u \
    /home/anonymous/program/resource_adaptive/tools/resume_xp_fresh0_fixed_contract_target10_v31.py \
    --bundle-root "${BUNDLE}" \
    --selection "${SEED_SELECTION}" \
    --replay "${SEED_REPLAY}" \
    --training-state "${TRAINING_STATE}" \
    --historical-accepted-excluded 4 \
    --campaign-root "${CAMPAIGN}" \
    --formal-manifest "${FORMAL}/formal_manifest.json" \
    --authority-counter "${FORMAL}/state/formal_search_accepted_counter.txt" \
    --authority-history "${FORMAL}/optimizer_history_2026-07-29_104036.jsonl" \
    --canonical-state "${CANONICAL_STATE}" \
    --canonical-manifest "${CANONICAL_MANIFEST}" \
    --acceptance-manifest "${FORMAL}/formal_manifest.json" \
    --query-snapshot "${BUNDLE}/authority_refresh_accepted4_v5_20260731/query_snapshot_v3.json" \
    --restore-script "${RESTORE}" \
    --resume-existing
