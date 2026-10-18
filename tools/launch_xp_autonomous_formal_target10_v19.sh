#!/usr/bin/env bash
set -euo pipefail

XP_BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v6
XP_PACKAGE=${XP_BUNDLE}/bundle_manifest_v19_safe_frontier.json
XP_EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
XP_ATTEMPT=${XP_EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
XP_FORMAL=${XP_ATTEMPT}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245
XP_CANONICAL=${XP_ATTEMPT}/formal10_canonical_after_unreserved_call_20260729T1448
XP_ACCEPTED4=${XP_ATTEMPT}/formal10_cont1_accepted4_cap5_feedback_v1_20260729T1729
XP_REPEAT=${XP_ATTEMPT}/gradient_debug_round17n_xp_exact_plan_repeat_v1_20260731T0345
XP_CONTROLLER=/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731
XP_PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3
XP_LOCK=/tmp/resource_adaptive_xp_formal_target10_v19.lock

exec 9>"${XP_LOCK}"
if ! flock -n 9; then
    printf '%s\n' \
        '{"status":"blocked_duplicate_controller","lock":"/tmp/resource_adaptive_xp_formal_target10_v19.lock"}' >&2
    exit 73
fi

XP_ACTIVE_PIDS="$(
    pgrep -f "${XP_BUNDLE}/[x]p_unattended_launch.py" 2>/dev/null \
        | paste -sd, - || true
)"
if test -n "${XP_ACTIVE_PIDS}"; then
    printf '{"status":"blocked_active_controller","pids":"%s"}\n' \
        "${XP_ACTIVE_PIDS}" >&2
    exit 74
fi

test "$("${XP_PYTHON}" -c 'import psycopg2; print("ok")')" = ok
test "$(sha256sum "${XP_PACKAGE}" | awk '{print $1}')" = \
    41130dfd35acc8e599529e0fcfb79f71ce97f80b96d28efd618faf9152d55caa
while IFS=$'\t' read -r XP_EXPECTED_SHA XP_RELATIVE_PATH; do
    test "$(sha256sum "${XP_BUNDLE}/${XP_RELATIVE_PATH}" | awk '{print $1}')" = \
        "${XP_EXPECTED_SHA}"
done < <(
    jq -r '.files | to_entries[] | [.value, .key] | @tsv' "${XP_PACKAGE}"
)
test "$(jq -r '.affinity.cpuset_cpus' "${XP_PACKAGE}")" = \
    "32-51,288-307"
test "$(jq -r '.affinity.excluded_mysql_cpus' "${XP_PACKAGE}")" = \
    "184-199,440-455"
test "$(jq -r '.probe_state_neutral' "${XP_PACKAGE}")" = true
test "$(jq -r '.unknown_plan_regime_trusted_eligible' "${XP_PACKAGE}")" = false

exec taskset -c 32-51,288-307 \
    "${XP_PYTHON}" -u "${XP_BUNDLE}/xp_unattended_launch.py" \
    --root "${XP_BUNDLE}" \
    --state "${XP_CONTROLLER}/state.json" \
    --batch-root "${XP_CONTROLLER}/batches" \
    --formal-manifest "${XP_FORMAL}/formal_manifest.json" \
    --authority-counter "${XP_FORMAL}/state/formal_search_accepted_counter.txt" \
    --authority-history "${XP_FORMAL}/optimizer_history_2026-07-29_225003.jsonl" \
    --canonical-state "${XP_CANONICAL}/canonical_state.json" \
    --canonical-manifest "${XP_CANONICAL}/manifest.json" \
    --acceptance-manifest "${XP_ACCEPTED4}/formal_manifest.json" \
    --query-snapshot "${XP_BUNDLE}/authority_refresh_accepted4_v5_20260731/query_snapshot_v3.json" \
    --replay-audit "${XP_REPEAT}/top5_strict_serial_diagnostic_v1/replay_summary.json" \
    --rank1-evidence "${XP_REPEAT}/top5_strict_serial_diagnostic_v1/rank_01/result.json" \
    --restore-script "${XP_EXPERIENCE}/restore_with_physical_io_reapply_attempt25.sh"
