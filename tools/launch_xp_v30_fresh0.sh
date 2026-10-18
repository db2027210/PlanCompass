#!/usr/bin/env bash
set -euo pipefail

BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v30_local_eta_rebase
CAMPAIGN=/home/anonymous/program/resource_adaptive/history/xp_final_algorithm_fresh10_v30_20260803T0935
TRAINING_STATE=${BUNDLE}/fresh0_initial_state.json
SEED_SELECTION=/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/selection.json
SEED_REPLAY=/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/replay_summary.json
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
BASE=${EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
FORMAL=${BASE}/formal10_after_v3g_entry_gate_20260729T1040
CANONICAL_STATE=${BUNDLE}/fresh0_initial_state.json
CANONICAL_MANIFEST=${BUNDLE}/fresh0_initialization_manifest.json
RESTORE=${EXPERIENCE}/restore_with_physical_io_reapply_attempt25.sh

EXPECTED_BUNDLE_MANIFEST_SHA256=52085dbf5a9f249302dfec9bae341940bb23f5ec03e8d60011553ebb9d2b7604
EXPECTED_RESTORE_SHA256=c0bd2e260ed0f92eb8181657c21184e0c37a6ed1f16c72411b26de69febe1787
EXPECTED_CONTROLLER_SHA256=ecb9aa72315725fa720095b321ae9d4c4e8fcdfd96f8014a69aba66c7b7db6a8
EXPECTED_TRAINING_STATE_SHA256=46c0efa1458056f4140c3275e88942a838212c4c3829d4a1e0ccbadcc65389ea
EXPECTED_INIT_MANIFEST_SHA256=2a8b8dc6f0c919d9a3c10a8cd7cd21b0ef558d6ab730d17f686231865754670b
EXPECTED_SEED_SELECTION_SHA256=5b984c1b2f77499686cdbde7d75b8f749fccfb4ed571c677cb1cbdb149124515
EXPECTED_SEED_REPLAY_SHA256=1146621365678c92d129293090af673403c5f923f7c4bf013ef8a125ba641118
EXPECTED_FORMAL_MANIFEST_SHA256=b36d2db6a013421f57c0a41922f62e676be841641b583986a8440d3134165081
EXPECTED_FORMAL_HISTORY_SHA256=fafb67e227b8015c01d680f392d8b5b33a98343c754dd86d2bde9c745fce9719
EXPECTED_ZERO_COUNTER_SHA256=9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = "${EXPECTED_BUNDLE_MANIFEST_SHA256}"
test "$(sha256sum "${RESTORE}" | awk '{print $1}')" = "${EXPECTED_RESTORE_SHA256}"
test "$(sha256sum /home/anonymous/program/resource_adaptive/tools/resume_xp_fresh0_fixed_contract_target10_v30.py | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA256}"
test "$(sha256sum "${TRAINING_STATE}" | awk '{print $1}')" = "${EXPECTED_TRAINING_STATE_SHA256}"
test "$(sha256sum "${BUNDLE}/fresh0_initialization_manifest.json" | awk '{print $1}')" = "${EXPECTED_INIT_MANIFEST_SHA256}"
test "$(sha256sum "${SEED_SELECTION}" | awk '{print $1}')" = "${EXPECTED_SEED_SELECTION_SHA256}"
test "$(sha256sum "${SEED_REPLAY}" | awk '{print $1}')" = "${EXPECTED_SEED_REPLAY_SHA256}"
test "$(sha256sum "${FORMAL}/formal_manifest.json" | awk '{print $1}')" = "${EXPECTED_FORMAL_MANIFEST_SHA256}"
test "$(sha256sum "${FORMAL}/optimizer_history_2026-07-29_104036.jsonl" | awk '{print $1}')" = "${EXPECTED_FORMAL_HISTORY_SHA256}"
test "$(sha256sum "${FORMAL}/state/formal_search_accepted_counter.txt" | awk '{print $1}')" = "${EXPECTED_ZERO_COUNTER_SHA256}"
test "$(<"${FORMAL}/state/formal_search_accepted_counter.txt")" = 0
/home/anonymous/miniconda3/envs/py310/bin/python3 \
    /home/anonymous/program/resource_adaptive/tools/verify_relative_sha256_manifest.py \
    --root "${BUNDLE}" \
    --manifest "${BUNDLE}/bundle_manifest.json"

test ! -e "${CAMPAIGN}"
jq -e '
    .accepted_count == 0 and
    .accepted_target == 10 and
    .historical_accepted_excluded == 4 and
    .incumbent_score == 4.93112215015808 and
    .status == "sealed_fresh0_initial_authority" and
    .gradient_state.update_count == 0 and
    .initial_authority.old_accepted4_state_used_for_incumbent_or_ranges == false
' "${TRAINING_STATE}" >/dev/null
jq -e '
    .algorithm_change_classification == "material" and
    .accepted_count_initial == 0 and
    .accepted_target == 10 and
    .historical_accepted_excluded == 4 and
    .historical_observations_formal_acceptance_eligible == false and
    .accepted4_state_used_for_incumbent_range_or_gradient == false and
    .future_material_algorithm_change_invalidates_campaign == true and
    .cpuset == "32-51,288-307"
' "${BUNDLE}/fresh0_initialization_manifest.json" >/dev/null

if [[ "${1:-}" == "--preflight-only" ]]; then
    echo "XP v30 fresh0 launch preflight passed"
    exit 0
fi
[[ "$#" -eq 0 ]]

exec taskset -c 32-51,288-307 \
    /home/anonymous/miniconda3/envs/py310/bin/python3 -u \
    /home/anonymous/program/resource_adaptive/tools/resume_xp_fresh0_fixed_contract_target10_v30.py \
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
    --restore-script "${RESTORE}"
