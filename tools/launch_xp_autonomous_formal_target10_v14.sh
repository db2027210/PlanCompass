#!/usr/bin/env bash
set -euo pipefail

BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v6
EXPERIENCE_ROOT=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
ATTEMPT=${EXPERIENCE_ROOT}/attempt39_fresh_from_accepted3_20260726
FORMAL=${ATTEMPT}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245
CANONICAL=${ATTEMPT}/formal10_canonical_after_unreserved_call_20260729T1448
ACCEPTED4=${ATTEMPT}/formal10_cont1_accepted4_cap5_feedback_v1_20260729T1729
REPEAT=${ATTEMPT}/gradient_debug_round17n_xp_exact_plan_repeat_v1_20260731T0345
CONTROLLER=/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3
LOCK=/tmp/resource_adaptive_xp_formal_target10_v14.lock

exec 9>"${LOCK}"
if ! flock -n 9; then
    printf '%s\n' \
        '{"status":"blocked_duplicate_controller","lock":"/tmp/resource_adaptive_xp_formal_target10_v14.lock"}' >&2
    exit 73
fi

ACTIVE_PIDS="$(
    pgrep -f "${BUNDLE}/[x]p_unattended_launch.py" 2>/dev/null \
        | paste -sd, - || true
)"
if test -n "${ACTIVE_PIDS}"; then
    printf '{"status":"blocked_active_controller","pids":"%s"}\n' "${ACTIVE_PIDS}" >&2
    exit 74
fi

test "$("${PYTHON}" -c 'import psycopg2; print("ok")')" = ok
test "$(sha256sum "${BUNDLE}/bundle_manifest_v12.json" | awk '{print $1}')" = \
    9a67e8a8fa30e1e6f2a64b2ea77e3aa5144e405814424e7c278670b384468db4
test "$(sha256sum "${BUNDLE}/xp_round10_calibrated_acquisition.py" | awk '{print $1}')" = \
    d2cfaf19a92cea4515b4d383ec7393741a08ab03a71073108e6081a0ef85c341
test "$(sha256sum "${BUNDLE}/xp_round10_diagnostic_bundle.py" | awk '{print $1}')" = \
    0b5863e0fa3b8349bbecc5f079ca96ce4214afec780ff257738e9f72e756261f
test "$(sha256sum "${BUNDLE}/xp_unattended_adapters.py" | awk '{print $1}')" = \
    0cbb08aa5645d4052daf83ce2cdf0b1689f34374236148364deafa5fb0b0c654
test "$(sha256sum "${BUNDLE}/xp_unattended_continuation.py" | awk '{print $1}')" = \
    4e7674781051c154ae4f1bb15dd87f68471e72b538215ea545a6e2b45a530f9f

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
    --restore-script "${EXPERIENCE_ROOT}/restore_with_physical_io_reapply_attempt25.sh"
