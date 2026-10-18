#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v15_plan_regime_quarantine
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
ATTEMPT=${EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v26_20260731T1935
STATE=${CAMPAIGN}/state.json
PREVIOUS=${CAMPAIGN}/batches/cont006/replay_summary.json
QUARANTINE=${CAMPAIGN}/quarantine_cont007_v14_invalid_scalar_cap_20260731T2357/quarantine_audit_v14.json

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = \
  1a902e94593615ae0308c944eb57c6a9477ae44666e3d0cf6fea389477a3f171
test "$(sha256sum "${BUNDLE}/xp_round10_explain_only_entrypoint.py" | awk '{print $1}')" = \
  68b31984a4a256fd6071f8f2af6ab7c6e9c4364c0ac18a89f6e963a53adbae87
test "$(sha256sum "${BUNDLE}/xp_unattended_adapters.py" | awk '{print $1}')" = \
  e68de45c0e468025650775a67446702a4bc42a0cd7505583a92efbff7d22fe7c
test "$(sha256sum "${BUNDLE}/xp_round10_diagnostic_bundle.py" | awk '{print $1}')" = \
  a59ecbe76e3d744c700851fae7779fc6c461b7d59705921ab4339200b3865040
test "$(sha256sum "${BUNDLE}/xp_unattended_continuation.py" | awk '{print $1}')" = \
  e7cc6cda6f5429afe24f4a9eed805060305f8893df8a0f18d1efaaf349c496b2
test "$(sha256sum "${PROJECT}/tools/resume_xp_fresh0_score_span_target10_v27.py" | awk '{print $1}')" = \
  ce3cb0704c3871fcd567e0896fd92ab4c3cfc1e05f635f887cc5f9e9d38999ad
test "$(sha256sum "${STATE}" | awk '{print $1}')" = \
  db560959a8966fc6137bb31ea3900b4a2085ea408a01a668836f3d893241cbaf
test "$(sha256sum "${QUARANTINE}" | awk '{print $1}')" = \
  0d8694fc6f8ae3664e1dd28770a1dddf8a81b06e8de3ea757558d608ca84520d

jq -e '
  .status == "blocked_contract" and
  .accepted_count == 0 and
  .accepted_target == 10 and
  .next_batch_index == 7 and
  .last_batch.batch_id == "cont006" and
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
  .contract == "xp_zero_workload_invalid_scalar_risk_model_quarantine_v1" and
  .physical_workload_calls == 0 and
  .accepted_delta == 0 and
  .formal_state_mutation == false
' "${QUARANTINE}" >/dev/null
test "$(sha256sum "${CAMPAIGN}/batches/cont007/selection.json" | awk '{print $1}')" = \
  feefcfd1735785eb45d32405b7abc0874f735e8d3038d714cb95a10bfcf681e9
test "$(sha256sum "${CAMPAIGN}/batches/cont007/sealed_zero_physical_resume_audit.json" | awk '{print $1}')" = \
  e15aad88be0ee453c34c5ecc53424bf31ec660e1f8b30c39db535d3ef36dff7b
test ! -e "${CAMPAIGN}/batches/cont007/replay_summary.json"
test ! -e "${CAMPAIGN}/batches/cont007/serial_replay"
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
