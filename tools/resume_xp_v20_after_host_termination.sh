#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v26_20260731T1935
BATCH=${CAMPAIGN}/batches/cont018
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v20_three_observation_median
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3

test "$(sha256sum "${CAMPAIGN}/state.json" | awk '{print $1}')" = \
    a50d37216063af985efc2984a560b155a491bc7b30ada33ca69279912e8ed85c
test "$(sha256sum "${BATCH}/selection.json" | awk '{print $1}')" = \
    77c8616f0b0600ab5c119d3fe9561b9a2994986fdceb2caf39cc466b8b880c95
test "$(sha256sum "${BATCH}/serial_replay/rank_01/result.json" | awk '{print $1}')" = \
    d57c9a73ced2338f2729b77fb4f2fb3640a617d21b040a61315d7c6170a7ae6f
test "$(sha256sum "${BATCH}/serial_replay/rank_02/result.json" | awk '{print $1}')" = \
    f66520129b0cad39c8e8ef84afc88b1b541c18b36e8e8d4fc049b6941e994875
test "$(sha256sum "${BATCH}/serial_replay/rank_03/result.json" | awk '{print $1}')" = \
    bd703260d78713cadd87b2ac3eaec4442a07fa1dc13196159a1763944266a564
test -f "${BATCH}/serial_replay/rank_04/candidate.json"
test -f "${BATCH}/serial_replay/rank_04/warmup.log"
test ! -e "${BATCH}/serial_replay/rank_04/result.json"
test ! -e "${BATCH}/serial_replay/rank_05"
test ! -e "${BATCH}/replay_summary.json"
jq -e '
  .status == "running" and .accepted_count == 2 and
  .accepted_target == 10 and .next_batch_index == 18
' "${CAMPAIGN}/state.json" >/dev/null

exec >>"${CAMPAIGN}/v20_host_termination_resume.log" 2>&1
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
