#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v16_fixed_contract_gate
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
ATTEMPT=${EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v26_20260731T1935
STATE=${CAMPAIGN}/state.json
SELECTION=${CAMPAIGN}/batches/cont008/selection.json
REPLAY=${CAMPAIGN}/batches/cont008/replay_summary.json
GATE=${CAMPAIGN}/cont008_fixed_contract_gate_recheck_v2.json
QUARANTINE=${CAMPAIGN}/quarantine_cont009_pre_fixed_gate_20260801T014134/quarantine_audit.json

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = \
  89fdd07fe113cfa5d2b89001b7e0139320c8e1dade668bfdab3aaab072c99186
test "$(sha256sum "${BUNDLE}/xp_unattended_adapters.py" | awk '{print $1}')" = \
  9b20c6bad2b71a4d12d9f8d6a7a6d636e4b788c2310d37120e756b8f6a945d0d
test "$(sha256sum "${BUNDLE}/xp_unattended_continuation.py" | awk '{print $1}')" = \
  e7cc6cda6f5429afe24f4a9eed805060305f8893df8a0f18d1efaaf349c496b2
test "$(sha256sum "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v28.py" | awk '{print $1}')" = \
  79bde33b61c5ecc990f8d91c356ffdf84b7f48d539275eff25a8aec8b429b42d
test "$(sha256sum "${PROJECT}/tools/verify_xp_fixed_contract_gate_v1.py" | awk '{print $1}')" = \
  845e3baad01b2ceaa1c14bee0cc1e170df3d7173c2a79e25ad85abf05f6791a3
test "$(sha256sum "${STATE}" | awk '{print $1}')" = \
  91e334effc4002806965ed4e1fa660d71e8dfe765f572f77dfb50d0c4d53c6bd
test "$(sha256sum "${SELECTION}" | awk '{print $1}')" = \
  7499ec4123fa456a96c1f444d95c284f34738ff7c311cd2a95a0b482116c815c
test "$(sha256sum "${REPLAY}" | awk '{print $1}')" = \
  4a7ce2e30b80223e8d3af553b01fac82d833433602172179046f55f755436e88
test "$(sha256sum "${GATE}" | awk '{print $1}')" = \
  c57aaeb36818cb2f7b5702f00dbe0161c800f44107534ac40d0a063ea8299a1b
test "$(sha256sum "${QUARANTINE}" | awk '{print $1}')" = \
  a830b9fcf56cd70a6aa9001522595832b0252c6d3358f86f229306dd23df829a

/home/anonymous/miniconda3/envs/py310/bin/python3 "${BUNDLE}/xp_round10_deploy.py" verify \
  --bundle-dir "${BUNDLE}" \
  --manifest "${BUNDLE}/bundle_manifest.json" >/dev/null

jq -e '
  .status == "running" and
  .accepted_count == 0 and
  .accepted_target == 10 and
  .next_batch_index == 9 and
  .last_batch.batch_id == "cont008" and
  .last_batch.mode == "diagnostic_validation" and
  .last_batch.physical_calls == 5 and
  .last_batch.technical_failures == 0 and
  .last_batch.accepted == 0
' "${STATE}" >/dev/null
jq -e '
  .passed == true and
  .contract == "xp_fresh0_formal_entry_recheck_fixed_contract_v2" and
  .trusted_spearman > 0 and
  .actual_best_predicted_rank <= 3 and
  .maximum_trusted_resource_factor_error < 10 and
  .checks.score_span_exceeds_replay_noise_bound == false and
  (.audit_only_checks == ["score_span_exceeds_replay_noise_bound"])
' "${GATE}" >/dev/null
jq -e '
  .physical_workload_invocation_count == 5 and
  .successful_real_replay_count == 5 and
  .technical_failure_count == 0 and
  .baseline_restore_verified == true and
  .diagnostic_only == true and
  .accepted_update_applied == false and
  .incumbent_updated == false and
  .range_updated == false and
  .gradient_update_applied == false
' "${REPLAY}" >/dev/null
jq -e '
  .physical_workload_calls == 0 and
  .accepted_delta == 0 and
  .formal_state_mutation == false
' "${QUARANTINE}" >/dev/null
test ! -e "${CAMPAIGN}/batches/cont009"
test -z "$(pgrep -f '^/home/anonymous/miniconda3/envs/py310/bin/python3 -u /home/anonymous/program/resource_adaptive/tools/resume_xp_fresh0_fixed_contract_target10_v28.py' || true)"

exec taskset -c 32-51,288-307 \
  /home/anonymous/miniconda3/envs/py310/bin/python3 -u \
  "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v28.py" \
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
