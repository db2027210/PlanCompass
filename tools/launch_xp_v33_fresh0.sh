#!/usr/bin/env bash
set -euo pipefail

BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=/home/anonymous/program/resource_adaptive/history/xp_final_algorithm_fresh10_v33_20260803T1155
TRAINING_STATE=${BUNDLE}/fresh0_initial_state.json
SEED_SELECTION=/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/selection.json
SEED_REPLAY=/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/replay_summary.json
TRAINING_SELECTION=/home/anonymous/program/resource_adaptive/history/xp_final_algorithm_fresh10_v32_20260803T1058/batches/cont001/selection.json
TRAINING_REPLAY=/home/anonymous/program/resource_adaptive/history/xp_final_algorithm_fresh10_v32_20260803T1058/batches/cont001/replay_summary.json
PRIOR_TRAINING_SELECTION=/home/anonymous/program/resource_adaptive/history/xp_final_algorithm_fresh10_v31_20260803T1009/batches/cont001/selection.json
PRIOR_TRAINING_REPLAY=/home/anonymous/program/resource_adaptive/history/xp_final_algorithm_fresh10_v31_20260803T1009/batches/cont001/replay_summary.json
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
BASE=${EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
FORMAL=${BASE}/formal10_after_v3g_entry_gate_20260729T1040
CANONICAL_STATE=${BUNDLE}/fresh0_initial_state.json
CANONICAL_MANIFEST=${BUNDLE}/fresh0_initialization_manifest.json
RESTORE=${EXPERIENCE}/restore_with_physical_io_reapply_attempt25.sh

EXPECTED_BUNDLE_MANIFEST_SHA256=323032499d52473b26e1db50ce0320dbb6951c4baae55fa72a4ac1d854277cb5
EXPECTED_RESTORE_SHA256=c0bd2e260ed0f92eb8181657c21184e0c37a6ed1f16c72411b26de69febe1787
EXPECTED_CONTROLLER_SHA256=364b5b28973d04a4fff039158de73ef7ce22e4519d4ebdb4e1c41942ebec94f8
EXPECTED_TRAINING_STATE_SHA256=f6f24906986e91a1cef1756dd75ce8cc738affc08afef2ebbdb246c73955a80f
EXPECTED_INIT_MANIFEST_SHA256=15a956be96a50ef0578a2d7589708af132b2f1f5a370ee986de021de7f34df37
EXPECTED_SEED_SELECTION_SHA256=5b984c1b2f77499686cdbde7d75b8f749fccfb4ed571c677cb1cbdb149124515
EXPECTED_SEED_REPLAY_SHA256=1146621365678c92d129293090af673403c5f923f7c4bf013ef8a125ba641118
EXPECTED_TRAINING_SELECTION_SHA256=3336ad8460c82f2ce503785cd2287a36bdab1ca88b745831435410ac8adc66d2
EXPECTED_TRAINING_REPLAY_SHA256=c2361b779b106eb1ddf97b3e428291ff5d8f8be3e9116e46a09d7ed4b59966f2
EXPECTED_PRIOR_TRAINING_SELECTION_SHA256=56063d026c31586997c8655221f5b01ca36b32c65f415a5cb74f8bf8a0bad017
EXPECTED_PRIOR_TRAINING_REPLAY_SHA256=d975d0047c6d0da3a8fbe613ff52ef2c37376c7149dc99a4254e323f05acb908
EXPECTED_FORMAL_MANIFEST_SHA256=b36d2db6a013421f57c0a41922f62e676be841641b583986a8440d3134165081
EXPECTED_FORMAL_HISTORY_SHA256=fafb67e227b8015c01d680f392d8b5b33a98343c754dd86d2bde9c745fce9719
EXPECTED_ZERO_COUNTER_SHA256=9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = "${EXPECTED_BUNDLE_MANIFEST_SHA256}"
test "$(sha256sum "${RESTORE}" | awk '{print $1}')" = "${EXPECTED_RESTORE_SHA256}"
test "$(sha256sum /home/anonymous/program/resource_adaptive/tools/resume_xp_fresh0_fixed_contract_target10_v33.py | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA256}"
test "$(sha256sum "${TRAINING_STATE}" | awk '{print $1}')" = "${EXPECTED_TRAINING_STATE_SHA256}"
test "$(sha256sum "${BUNDLE}/fresh0_initialization_manifest.json" | awk '{print $1}')" = "${EXPECTED_INIT_MANIFEST_SHA256}"
test "$(sha256sum "${SEED_SELECTION}" | awk '{print $1}')" = "${EXPECTED_SEED_SELECTION_SHA256}"
test "$(sha256sum "${SEED_REPLAY}" | awk '{print $1}')" = "${EXPECTED_SEED_REPLAY_SHA256}"
test "$(sha256sum "${TRAINING_SELECTION}" | awk '{print $1}')" = "${EXPECTED_TRAINING_SELECTION_SHA256}"
test "$(sha256sum "${TRAINING_REPLAY}" | awk '{print $1}')" = "${EXPECTED_TRAINING_REPLAY_SHA256}"
test "$(sha256sum "${PRIOR_TRAINING_SELECTION}" | awk '{print $1}')" = "${EXPECTED_PRIOR_TRAINING_SELECTION_SHA256}"
test "$(sha256sum "${PRIOR_TRAINING_REPLAY}" | awk '{print $1}')" = "${EXPECTED_PRIOR_TRAINING_REPLAY_SHA256}"
test "$(sha256sum "${FORMAL}/formal_manifest.json" | awk '{print $1}')" = "${EXPECTED_FORMAL_MANIFEST_SHA256}"
test "$(sha256sum "${FORMAL}/optimizer_history_2026-07-29_104036.jsonl" | awk '{print $1}')" = "${EXPECTED_FORMAL_HISTORY_SHA256}"
test "$(sha256sum "${FORMAL}/state/formal_search_accepted_counter.txt" | awk '{print $1}')" = "${EXPECTED_ZERO_COUNTER_SHA256}"
test "$(<"${FORMAL}/state/formal_search_accepted_counter.txt")" = 0
jq -e '
    (.top5 | length) == 5 and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    ([.top5[].boundary_corner] | all(. == false)) and
    ([.top5[].selection_role] | all(. == "maximin_uncertain_probe"))
' "${TRAINING_SELECTION}" >/dev/null
jq -e '
    .physical_workload_invocation_count == 5 and
    .successful_real_replay_count == 5 and
    .technical_failure_count == 0 and
    (.successful | length) == 5 and
    (.technical_failures | length) == 0 and
    (.rejected | length) == 0 and
    .baseline_restore_verified == true and
    .accepted_update_applied == false and
    .gradient_update_applied == false and
    .incumbent_updated == false and
    .range_updated == false and
    .state_mutations_applied_by_this_bundle == false and
    ([.successful[].cpuset_cpus] | unique) == ["32-51,288-307"]
' "${TRAINING_REPLAY}" >/dev/null
/home/anonymous/miniconda3/envs/py310/bin/python3 \
    /home/anonymous/program/resource_adaptive/tools/verify_relative_sha256_manifest.py \
    --root "${BUNDLE}" \
    --manifest "${BUNDLE}/bundle_manifest.json"

test ! -e "${CAMPAIGN}"
jq -e '
    .accepted_count == 0 and
    .accepted_target == 10 and
    .historical_accepted_excluded == 4 and
    .incumbent_score == null and
    .status == "sealed_fresh0_initial_authority" and
    .gradient_state.update_count == 0 and
    .initial_authority.old_accepted4_state_used_for_incumbent_or_ranges == false and
    .initial_authority.historical_scores_formal_acceptance_eligible == false and
    .initial_authority.current_runtime_baseline_revalidated == false and
    .initial_authority.old_sealed_baseline_role == "training_and_audit_only" and
    .initial_authority.fresh_score_regime == "xp_v33_fresh0_band_target_v1" and
    .initial_authority.material_algorithm_epoch == "v33_plan_quarantine_transition_fail_closed"
' "${TRAINING_STATE}" >/dev/null
jq -e '
    .algorithm_change_classification == "material" and
    .accepted_count_initial == 0 and
    .accepted_target == 10 and
    .historical_accepted_excluded == 4 and
    .historical_observations_formal_acceptance_eligible == false and
    .accepted4_state_used_for_incumbent_range_or_gradient == false and
    .future_material_algorithm_change_invalidates_campaign == true and
    .score_domain_isolation.historical_seed_scores_formal_admission_eligible == false and
    .score_domain_isolation.fresh_real_observation_required_before_formal_promotion == true and
    .cpuset == "32-51,288-307"
' "${BUNDLE}/fresh0_initialization_manifest.json" >/dev/null

if [[ "${1:-}" == "--preflight-only" ]]; then
    echo "XP v33 fresh-runtime-R0 compound-regime launch preflight passed"
    exit 0
fi
[[ "$#" -eq 0 ]]

exec taskset -c 32-51,288-307 \
    /home/anonymous/miniconda3/envs/py310/bin/python3 -u \
    /home/anonymous/program/resource_adaptive/tools/resume_xp_fresh0_fixed_contract_target10_v33.py \
    --bundle-root "${BUNDLE}" \
    --selection "${SEED_SELECTION}" \
    --replay "${SEED_REPLAY}" \
    --training-selection "${TRAINING_SELECTION}" \
    --training-replay "${TRAINING_REPLAY}" \
    --prior-training-selection "${PRIOR_TRAINING_SELECTION}" \
    --prior-training-replay "${PRIOR_TRAINING_REPLAY}" \
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
