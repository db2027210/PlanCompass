#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v19_two_observation
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
ATTEMPT=${EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v26_20260731T1935
STATE=${CAMPAIGN}/state.json
RECOVERY=${CAMPAIGN}/quarantine_cont015_safe_diversity_20260801T095427/recovery_audit.json
CONNECT_RECOVERY=${CAMPAIGN}/quarantine_cont015_pg_connect_20260801T095839/recovery_audit.json
AXIS_RECOVERY=${CAMPAIGN}/quarantine_cont015_axis_trace_20260801T101232/recovery_audit.json

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = \
  e8f8744b46be1b9671ef8c839edcce5bee045048254b6ddc831f3db1f5591b5a
test "$(sha256sum "${BUNDLE}/xp_round10_explain_only_entrypoint.py" | awk '{print $1}')" = \
  c586095edb443836da170c13f74843bfc18ca3ff4e2d9608736f543676ddee39
test "$(sha256sum "${BUNDLE}/build_xp_exact_plan_repeat_selection.py" | awk '{print $1}')" = \
  fb92dbc2b1289b89aa7d53f9e779c2bbe1bb323ab26960130ee9129aa42cd6ed
test "$(sha256sum "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v28.py" | awk '{print $1}')" = \
  79bde33b61c5ecc990f8d91c356ffdf84b7f48d539275eff25a8aec8b429b42d
test "$(sha256sum "${STATE}" | awk '{print $1}')" = \
  312bd3feffe77eb8a725adaadb885da7798f968baa00b0dcf1a3a4c4864cf391
test "$(sha256sum "${RECOVERY}" | awk '{print $1}')" = \
  a028b26dd4352ba2e6937625f5a0ea815ef77db7cbb1efb613ff020750bc7c36
test "$(sha256sum "${CONNECT_RECOVERY}" | awk '{print $1}')" = \
  00014c04c213b9906cc900074e9de40456f579d3760c2b7fa681c9b0214a53b3
test "$(sha256sum "${AXIS_RECOVERY}" | awk '{print $1}')" = \
  b3efa4ac44a3a0974afa2a3f5910adb7af60682aaa401f53f42b3bd78e8b6efd

/home/anonymous/miniconda3/envs/py310/bin/python3 \
  "${BUNDLE}/xp_round10_deploy.py" verify \
  --bundle-dir "${BUNDLE}" \
  --manifest "${BUNDLE}/bundle_manifest.json" >/dev/null

jq -e '
  .status == "running" and
  .accepted_count == 1 and
  .accepted_target == 10 and
  .next_batch_index == 15 and
  .last_error == null and
  .last_batch.batch_id == "cont014" and
  .last_batch.mode == "diagnostic_validation" and
  .last_batch.status == "complete" and
  .last_batch.physical_calls == 5 and
  .last_batch.technical_failures == 0 and
  .last_batch.accepted == 0
' "${STATE}" >/dev/null
jq -e '
  .status == "reopened_same_batch_after_algorithm_fix" and
  .accepted_count == 1 and
  .next_batch_index == 15 and
  .physical_workload_calls == 0 and
  .valid_replays == 0 and
  .technical_failures == 0 and
  .formal_state_mutated == false
' "${RECOVERY}" >/dev/null
jq -e '
  .status == "reopened_same_batch_after_bounded_retry_fix" and
  .accepted_count == 1 and
  .next_batch_index == 15 and
  .physical_workload_calls == 0 and
  .valid_replays == 0 and
  .technical_failures == 0 and
  .formal_state_mutated == false
' "${CONNECT_RECOVERY}" >/dev/null
jq -e '
  .status == "reopened_same_batch_after_axis_trace_fix" and
  .accepted_count == 1 and
  .next_batch_index == 15 and
  .physical_workload_calls == 0 and
  .valid_replays == 0 and
  .technical_failures == 0 and
  .formal_state_mutated == false
' "${AXIS_RECOVERY}" >/dev/null
test ! -e "${CAMPAIGN}/batches/cont015"
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
