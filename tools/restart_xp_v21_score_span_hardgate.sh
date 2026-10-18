#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v26_20260731T1935
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v21_score_span_hardgate
AUDIT=${CAMPAIGN}/batches/cont020/invalid_formal_entry_reclassification.json
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3

test -f "${AUDIT}"
test "$(sha256sum "${CAMPAIGN}/state.json" | awk '{print $1}')" = \
    "$(jq -r '.state_sha256_after' "${AUDIT}")"
test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = \
    a59d54636607e6c3335b876afda7d89a67c419c004ca0edc8d12d743ebcf7476
test "$(sha256sum "${BUNDLE}/xp_unattended_adapters.py" | awk '{print $1}')" = \
    bee76df8415cc612ddcccb892e7dfd568683497c4f182f3f67aa79a362211477
test "$(sha256sum "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v28.py" | awk '{print $1}')" = \
    4200511566ae147040396f4fec292ec863c7305b47a68dd49c42d675d6fa7831
jq -e '
  .status == "running" and .accepted_count == 2 and
  .accepted_target == 10 and .next_batch_index == 21 and
  .last_batch.batch_id == "cont020" and
  .last_batch.status == "reclassified_invalid_formal_entry_gate"
' "${CAMPAIGN}/state.json" >/dev/null
jq -e '
  .formal_state_mutated == false and .batch_added_accepted == 0 and
  .physical_workload_calls == 1 and .valid_replays == 1 and
  .technical_failures == 0 and .baseline_restored == true and
  .score_span <= .noise_bound
' "${AUDIT}" >/dev/null

exec "${PYTHON}" -u "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v28.py" \
    --resume-existing \
    --bundle-root "${BUNDLE}" \
    --selection "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/selection.json" \
    --replay "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/batches/cont007/replay_summary.json" \
    --training-state "${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/state.json" \
    --campaign-root "${CAMPAIGN}" \
    --formal-manifest /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/formal_manifest.json \
    --authority-counter /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/state/formal_search_accepted_counter.txt \
    --authority-history /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/optimizer_history_2026-07-29_225003.jsonl \
    --canonical-state /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_canonical_after_unreserved_call_20260729T1448/canonical_state.json \
    --canonical-manifest /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_canonical_after_unreserved_call_20260729T1448/manifest.json \
    --acceptance-manifest /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont1_accepted4_cap5_feedback_v1_20260729T1729/formal_manifest.json \
    --query-snapshot "${BUNDLE}/authority_refresh_accepted4_v5_20260731/query_snapshot_v3.json" \
    --restore-script /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/restore_with_physical_io_reapply_attempt25.sh
