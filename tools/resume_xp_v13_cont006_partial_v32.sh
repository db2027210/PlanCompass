#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v13_calibrated_safe_diversity
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
ATTEMPT=${EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v26_20260731T1935
STATE=${CAMPAIGN}/state.json
BATCH=${CAMPAIGN}/batches/cont006
SELECTION=${BATCH}/selection.json

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = \
  3576d9ddcedc860577fa89557b8f81c4203c05519c1a197062ba285bebe260e6
test "$(sha256sum "${PROJECT}/tools/resume_xp_fresh0_score_span_target10_v27.py" | awk '{print $1}')" = \
  ce3cb0704c3871fcd567e0896fd92ab4c3cfc1e05f635f887cc5f9e9d38999ad
test "$(sha256sum "${STATE}" | awk '{print $1}')" = \
  bc69e86c9c4772831f7f4599e53959638fd9088d74d4efac117f487a6a5963e9
test "$(sha256sum "${SELECTION}" | awk '{print $1}')" = \
  b11fb6803b1ea33c6700a5a94f81b24d9076a847dbd523bb365377da7b498d6a

jq -e '
  .accepted_count == 0 and
  .accepted_target == 10 and
  .next_batch_index == 6 and
  .status == "blocked_contract"
' "${STATE}" >/dev/null
jq -e '.physical_call == true and .technical_failure == false' \
  "${BATCH}/serial_replay/rank_01/result.json" \
  "${BATCH}/serial_replay/rank_02/result.json" >/dev/null
test -f "${BATCH}/serial_replay/rank_03/warmup.log"
test ! -e "${BATCH}/serial_replay/rank_03/result.json"
test ! -e "${BATCH}/serial_replay/rank_04/result.json"
test ! -e "${BATCH}/serial_replay/rank_05/result.json"
test ! -e "${BATCH}/replay_summary.json"
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
