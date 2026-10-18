#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v36_20260803
FORMAL=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_after_v3g_entry_gate_20260729T1040
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
CONTROLLER=${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v36.py
PREPARE=${PROJECT}/tools/prepare_xp_v36_from_v35_zero_call.py

test "$(sha256sum "${CONTROLLER}" | awk '{print $1}')" = 5a2617884b0cce051480644548f90e673ad023e894b2ad563d9871ee1aecb90c
test "$(sha256sum "${PREPARE}" | awk '{print $1}')" = 44e42928ffc78e4a04e1ab3b9ea71963a12578fad8465d0aa0dc9938cbe2847e
test "$(sha256sum "${BUNDLE}/xp_unattended_adapters.py" | awk '{print $1}')" = 7db2d54adba6aad1191970dda51a154937a761caff8c9ce152608985e54e510a
test "$(sha256sum "${BUNDLE}/xp_round10_diagnostic_bundle.py" | awk '{print $1}')" = c91fa48a7e79f9a1307b2e3eec4a240809861e08472824aba7b8cfffaf71246e
test "$(sha256sum "${BUNDLE}/xp_unattended_continuation.py" | awk '{print $1}')" = 6b7b7d3364c290af2ec65b672ac61c96b1308a8d8f78d0b6b9668ce3e58c0604

if pgrep -af 'resume_xp_fresh0_fixed_contract_target10|xp_round9_serial_replay|xp_legacy_replay_driver' >/dev/null; then
    echo "active XP controller/replay detected; refusing overlap" >&2
    exit 73
fi

if ! test -f "${CAMPAIGN}/state.json"; then
    /home/anonymous/miniconda3/envs/py310/bin/python3 -u "${PREPARE}" \
        > /tmp/xp_v36_prepare_output.json
fi
jq -e '
    .status == "initialized_fresh0_runtime_anchor_ready" and
    .accepted_count == 0 and .accepted_target == 10 and
    .next_batch_index == 1 and
    .gradient_state.update_count == 0 and
    .last_batch.batch_id == "r0fresh000" and
    .fresh_runtime_baseline_revalidation.status == "complete" and
    .v36_zero_call_handoff.workload_calls_added == 0 and
    .v36_zero_call_handoff.formal_state_mutations == 0
' "${CAMPAIGN}/state.json" >/dev/null
jq -e '
    .replay_authorized == true and
    .batch_mode == "diagnostic_validation" and
    (.top5 | length) == 5 and
    ([.top5[].selection_role] | all(. == "maximin_uncertain_probe")) and
    ([.top5[].boundary_corner] | all(. == false)) and
    .v36_resource_safe_maximin_fallback.new_workload_calls == 0 and
    .v36_resource_safe_maximin_fallback.trusted_promotion_applied == false and
    .v36_resource_safe_maximin_fallback.resource_envelope_relaxed == false
' "${CAMPAIGN}/batches/cont001/selection.json" >/dev/null
jq -e '
    .status == "consumed" and .maximum_fallback_batches == 1 and
    .fallback_batches_used == 1 and .workload_calls == 0 and
    .formal_state_mutations == 0
' "${CAMPAIGN}/v36_safe_probe_fallback_budget.json" >/dev/null

if [[ "${1:-}" == "--preflight-only" ]]; then
    echo "XP v36 bounded safe-probe fallback preflight passed"
    exit 0
fi
[[ "$#" -eq 0 ]]

exec taskset -c 32-51,288-307 \
    /home/anonymous/miniconda3/envs/py310/bin/python3 -u \
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
