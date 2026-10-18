#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v13_calibrated_safe_diversity
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
ATTEMPT=${EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v26_20260731T1935
STATE=${CAMPAIGN}/state.json
PREVIOUS=${CAMPAIGN}/batches/cont005/replay_summary.json
QUARANTINE=${CAMPAIGN}/quarantine_cont006_v12_safe_probe_shortfall_20260731T2240/quarantine_audit.json

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = \
  3576d9ddcedc860577fa89557b8f81c4203c05519c1a197062ba285bebe260e6
test "$(sha256sum "${BUNDLE}/xp_round10_explain_only_entrypoint.py" | awk '{print $1}')" = \
  68b31984a4a256fd6071f8f2af6ab7c6e9c4364c0ac18a89f6e963a53adbae87
test "$(sha256sum "${BUNDLE}/xp_unattended_adapters.py" | awk '{print $1}')" = \
  25c3b0c20a6e48720498aeb729a6f8ad9a74834416f2b89d7aca16b861af2df2
test "$(sha256sum "${BUNDLE}/xp_unattended_continuation.py" | awk '{print $1}')" = \
  5ecdec6bdea05424600a1c2e6ef807d1937806baf64334628e734a6995336bf6
test "$(sha256sum "${PROJECT}/tools/resume_xp_fresh0_score_span_target10_v27.py" | awk '{print $1}')" = \
  ce3cb0704c3871fcd567e0896fd92ab4c3cfc1e05f635f887cc5f9e9d38999ad
test "$(sha256sum "${STATE}" | awk '{print $1}')" = \
  bc69e86c9c4772831f7f4599e53959638fd9088d74d4efac117f487a6a5963e9
test "$(sha256sum "${QUARANTINE}" | awk '{print $1}')" = \
  4b5fb2edcd95e9bf752c72924ab05338d3bf69c42058601c1d185b9764481c43

jq -e '
  .status == "blocked_contract" and
  .accepted_count == 0 and
  .accepted_target == 10 and
  .next_batch_index == 6 and
  .last_batch.batch_id == "cont005" and
  .last_batch.mode == "diagnostic_validation" and
  .last_batch.physical_calls == 5 and
  .last_batch.technical_failures == 1 and
  .last_batch.accepted == 0
' "${STATE}" >/dev/null
jq -e '
  .physical_workload_invocation_count == 5 and
  .successful_real_replay_count == 4 and
  .technical_failure_count == 1 and
  .accepted_count_delta == 0 and
  .baseline_restore_verified == true and
  .diagnostic_only == true and
  .accepted_update_applied == false and
  .incumbent_updated == false and
  .range_updated == false and
  .gradient_update_applied == false
' "${PREVIOUS}" >/dev/null
jq -e '
  .contract == "xp_zero_workload_safe_probe_shortfall_quarantine_v1" and
  .physical_workload_calls == 0 and
  .accepted_delta == 0 and
  .formal_state_mutation == false
' "${QUARANTINE}" >/dev/null
test ! -e "${CAMPAIGN}/batches/cont006"
test -z "$(pgrep -f '^/home/anonymous/miniconda3/envs/py310/bin/python3 -u /home/anonymous/program/resource_adaptive/tools/resume_xp_fresh0_score_span_target10_v27.py' || true)"

exec taskset -c 32-51,288-307 \
  /home/anonymous/miniconda3/envs/py310/bin/python3 -u \
  "${PROJECT}/tools/resume_xp_fresh0_score_span_target10_v27.py" \
  --resume-existing \
  --bundle-root "${BUNDLE}" \
  --selection "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/selection.json" \
  --replay "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/replay_summary.json" \
  --training-state "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/state.json" \
  --campaign-root "${CAMPAIGN}" \
  --formal-manifest "${ATTEMPT}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/formal_manifest.json" \
  --authority-counter "${ATTEMPT}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/state/formal_search_accepted_counter.txt" \
  --authority-history "${ATTEMPT}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/optimizer_history_2026-07-29_225003.jsonl" \
  --canonical-state "${ATTEMPT}/formal10_canonical_after_unreserved_call_20260729T1448/canonical_state.json" \
  --canonical-manifest "${ATTEMPT}/formal10_canonical_after_unreserved_call_20260729T1448/manifest.json" \
  --acceptance-manifest "${ATTEMPT}/formal10_cont1_accepted4_cap5_feedback_v1_20260729T1729/formal_manifest.json" \
  --query-snapshot "${BUNDLE}/authority_refresh_accepted4_v5_20260731/query_snapshot_v3.json" \
  --restore-script "${EXPERIENCE}/restore_with_physical_io_reapply_attempt25.sh"
