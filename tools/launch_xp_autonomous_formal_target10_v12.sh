#!/usr/bin/env bash
set -euo pipefail

BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v6
ATTEMPT=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726
FORMAL=${ATTEMPT}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245
CANONICAL=${ATTEMPT}/formal10_canonical_after_unreserved_call_20260729T1448
ACCEPTED4=${ATTEMPT}/formal10_cont1_accepted4_cap5_feedback_v1_20260729T1729
REPEAT=${ATTEMPT}/gradient_debug_round17n_xp_exact_plan_repeat_v1_20260731T0345
CONTROLLER=/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3

test "$("${PYTHON}" -c 'import psycopg2; print("ok")')" = ok
test "$(sha256sum "${BUNDLE}/bundle_manifest_v12.json" | awk '{print $1}')" = \
    613bc14de6ddae5f0913a92cc97e0f9a92a8ea6ccd4e12c891d5b7c391030f0a
test "$(sha256sum "${BUNDLE}/xp_unattended_adapters.py" | awk '{print $1}')" = \
    62ab9e503dffd4f3f563ee2a1a002b2572324edd0ea8b16c9a7fdca404240fbf
test "$(sha256sum "${BUNDLE}/xp_unattended_continuation.py" | awk '{print $1}')" = \
    92e801258e1ef1f37cda623b0db0d241cf40b24bcfb916ea809d96f6f30580ef

exec taskset -c 32-51,288-307 \
    "${PYTHON}" -u "${BUNDLE}/xp_unattended_launch.py" \
    --root "${BUNDLE}" \
    --state "${CONTROLLER}/state.json" \
    --batch-root "${CONTROLLER}/batches" \
    --formal-manifest "${FORMAL}/formal_manifest.json" \
    --authority-counter "${FORMAL}/state/formal_search_accepted_counter.txt" \
    --authority-history "${FORMAL}/optimizer_history_2026-07-29_225003.jsonl" \
    --canonical-state "${CANONICAL}/canonical_state.json" \
    --canonical-manifest "${CANONICAL}/manifest.json" \
    --acceptance-manifest "${ACCEPTED4}/formal_manifest.json" \
    --query-snapshot "${BUNDLE}/authority_refresh_accepted4_v4_20260731/query_snapshot_v3.json" \
    --replay-audit "${REPEAT}/top5_strict_serial_diagnostic_v1/replay_summary.json" \
    --rank1-evidence "${REPEAT}/top5_strict_serial_diagnostic_v1/rank_01/result.json" \
    --restore-script /disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/restore_with_physical_io_reapply_attempt25.sh
