#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v17_sequential_range_admission
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
ATTEMPT=${EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v26_20260731T1935
STATE=${CAMPAIGN}/state.json
SELECTION=${CAMPAIGN}/batches/cont009/selection.json
REPLAY=${CAMPAIGN}/batches/cont009/replay_summary.json
REPAIR=${CAMPAIGN}/batches/cont009/state_patch_repair_transaction_v2.json

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = \
  ef449fe2d8c9711858a0f00f4ea553838c339a786acb0d97c7cee08a33833c3d
test "$(sha256sum "${BUNDLE}/xp_unattended_adapters.py" | awk '{print $1}')" = \
  2e5c65756d52b9e2fcd6644f83308ba213d9482ea17ef17478698c1a70af026e
test "$(sha256sum "${BUNDLE}/xp_unattended_continuation.py" | awk '{print $1}')" = \
  9b7bd16351e2cd5c07801a3af843750a793ffedbbff16b3cde717485fe91f883
test "$(sha256sum "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v28.py" | awk '{print $1}')" = \
  79bde33b61c5ecc990f8d91c356ffdf84b7f48d539275eff25a8aec8b429b42d
test "$(sha256sum "${PROJECT}/tools/verify_xp_fixed_contract_gate_v1.py" | awk '{print $1}')" = \
  845e3baad01b2ceaa1c14bee0cc1e170df3d7173c2a79e25ad85abf05f6791a3
test "$(sha256sum "${STATE}" | awk '{print $1}')" = \
  24a6005c4fca274309e8d210d0b76bd405f1e3b94ae7500fac485243f2954b63
test "$(sha256sum "${SELECTION}" | awk '{print $1}')" = \
  4a4bf06a25dd728e4a34bd0bd30605d493b77533987de23c41fa348df6a87307
test "$(sha256sum "${REPLAY}" | awk '{print $1}')" = \
  884e80f16fc40eba1ef759473982b14acf2bba5dd70293bf9881145881d5c38d
test "$(sha256sum "${REPAIR}" | awk '{print $1}')" = \
  8bbaf106407d2a556a29f316efc79c519f8d46de56929a9e6833320215bfac51

/home/anonymous/miniconda3/envs/py310/bin/python3 "${BUNDLE}/xp_round10_deploy.py" verify \
  --bundle-dir "${BUNDLE}" \
  --manifest "${BUNDLE}/bundle_manifest.json" >/dev/null

jq -e '
  .status == "running" and
  .accepted_count == 1 and
  .accepted_target == 10 and
  .next_batch_index == 10 and
  .last_batch.batch_id == "cont009" and
  .last_batch.mode == "formal" and
  .last_batch.physical_calls == 5 and
  .last_batch.technical_failures == 0 and
  .last_batch.accepted == 1 and
  .last_batch.rejected == 2 and
  .last_batch.state_patch_repaired == true
' "${STATE}" >/dev/null
jq -e '
  .physical_workload_invocation_count == 5 and
  .successful_real_replay_count == 5 and
  .technical_failure_count == 0 and
  .baseline_restore_verified == true and
  .diagnostic_only == false and
  .accepted_count_delta == 1 and
  .accepted_update_applied == true and
  .incumbent_updated == true and
  .range_updated == true and
  .gradient_update_applied == true and
  ([.ledger[] | select(.accepted == true)] | length) == 1 and
  ([.ledger[] | select(.rejected == true)] | length) == 2
' "${REPLAY}" >/dev/null
jq -e '
  .physical_workload_calls_added == 0 and
  .accepted_count_before == 1 and
  .accepted_count_after == 1 and
  .status == "committed"
' "${REPAIR}" >/dev/null
test ! -e "${CAMPAIGN}/batches/cont010"
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
