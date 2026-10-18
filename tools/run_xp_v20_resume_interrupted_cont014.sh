#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v19_two_observation
EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
ATTEMPT=${EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v26_20260731T1935
STATE=${CAMPAIGN}/state.json
BATCH=${CAMPAIGN}/batches/cont014
SERIAL=${BATCH}/serial_replay

test "$(sha256sum "${BUNDLE}/bundle_manifest.json" | awk '{print $1}')" = \
  c13dae49b2eb0b3389ca9c566c659e24169d096bcc4096d838ef31face7a427c
test "$(sha256sum "${BUNDLE}/build_xp_exact_plan_repeat_selection.py" | awk '{print $1}')" = \
  fb92dbc2b1289b89aa7d53f9e779c2bbe1bb323ab26960130ee9129aa42cd6ed
test "$(sha256sum "${PROJECT}/tools/resume_xp_fresh0_fixed_contract_target10_v28.py" | awk '{print $1}')" = \
  79bde33b61c5ecc990f8d91c356ffdf84b7f48d539275eff25a8aec8b429b42d
test "$(sha256sum "${STATE}" | awk '{print $1}')" = \
  58b74926543461213bc9616e18aa530b266aba0b47a860fb60c35be758d053b3
test "$(sha256sum "${BATCH}/selection.json" | awk '{print $1}')" = \
  4c3115a78543ae592e38b06610c0508502f4c64dcd7cc8c71319996a43f23188
test "$(sha256sum "${SERIAL}/rank_01/result.json" | awk '{print $1}')" = \
  0a2e3a183c6599e5f84cfd82e180cd83bba22f288000740b6eb6da7290b62618
test "$(sha256sum "${SERIAL}/rank_02/result.json" | awk '{print $1}')" = \
  9935292ba29401c76ebac2108a639fa4c3ed75dee525b20841044fa1654666e8
test "$(sha256sum "${SERIAL}/rank_03/result.json" | awk '{print $1}')" = \
  01f84e8d6b6b914a6680d3d14714178e1b8a978b9a040ac9cbd7f2012d724d02

"/home/anonymous/miniconda3/envs/py310/bin/python3" \
  "${BUNDLE}/xp_round10_deploy.py" verify \
  --bundle-dir "${BUNDLE}" \
  --manifest "${BUNDLE}/bundle_manifest.json" >/dev/null

jq -e '
  .status == "blocked_contract" and
  .accepted_count == 1 and
  .accepted_target == 10 and
  .next_batch_index == 14 and
  .last_batch.batch_id == "cont013" and
  .last_batch.status == "complete"
' "${STATE}" >/dev/null

for rank in 01 02 03; do
  jq -e '
    .physical_call == true and
    .technical_failure == false and
    .execution == "ran_once"
  ' "${SERIAL}/rank_${rank}/result.json" >/dev/null
done

# rank4 crossed the physical boundary but has no durable result.  The sealed
# serial runner must convert it to one technical failure and must never rerun it.
test -f "${SERIAL}/rank_04/candidate.json"
test -f "${SERIAL}/rank_04/recovery.log"
test -f "${SERIAL}/rank_04/warmup.log"
test ! -e "${SERIAL}/rank_04/result.json"
test ! -e "${SERIAL}/rank_05/result.json"
test ! -e "${BATCH}/replay_summary.json"
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
