#!/usr/bin/env bash
set -euo pipefail

XP_BUNDLE=${XP_LAUNCH_BUNDLE:-/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v6}
XP_PACKAGE_VERSION=${XP_LAUNCH_PACKAGE_VERSION:-v20}
case "${XP_PACKAGE_VERSION}" in
    v20)
        XP_EXPECTED_PACKAGE_SHA=967cd372045d01e274e1db596c375e75191ae495b9e90a822e64806e572273f7
        ;;
    v21)
        XP_EXPECTED_PACKAGE_SHA=7583bcbaa489aeb00e40545403b6c03b7066582175ae672a35551f7b87eed13a
        ;;
    v22)
        XP_EXPECTED_PACKAGE_SHA=${XP_EXPECTED_PACKAGE_SHA_V22:?missing XP_EXPECTED_PACKAGE_SHA_V22}
        ;;
    v23)
        XP_EXPECTED_PACKAGE_SHA=${XP_EXPECTED_PACKAGE_SHA_V23:?missing XP_EXPECTED_PACKAGE_SHA_V23}
        ;;
    v24)
        XP_EXPECTED_PACKAGE_SHA=${XP_EXPECTED_PACKAGE_SHA_V24:?missing XP_EXPECTED_PACKAGE_SHA_V24}
        ;;
    v25)
        XP_EXPECTED_PACKAGE_SHA=${XP_EXPECTED_PACKAGE_SHA_V25:?missing XP_EXPECTED_PACKAGE_SHA_V25}
        ;;
    *)
        echo "unsupported XP package version: ${XP_PACKAGE_VERSION}" >&2
        exit 64
        ;;
esac
XP_PACKAGE=${XP_BUNDLE}/bundle_manifest_${XP_PACKAGE_VERSION}_safe_frontier.json
XP_EXPERIENCE=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd
XP_ATTEMPT=${XP_EXPERIENCE}/attempt39_fresh_from_accepted3_20260726
XP_FORMAL=${XP_ATTEMPT}/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245
XP_CANONICAL=${XP_ATTEMPT}/formal10_canonical_after_unreserved_call_20260729T1448
XP_ACCEPTED4=${XP_ATTEMPT}/formal10_cont1_accepted4_cap5_feedback_v1_20260729T1729
XP_CONTROLLER=/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731
XP_BOOTSTRAP_SELECTION=/tmp/xp_incumbent_revalidation_candidate_v16_20260731.json
XP_BOOTSTRAP_REPLAY=/tmp/xp_incumbent_revalidation_once_v16_20260731/summary.json
XP_PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3
XP_LOCK=/tmp/resource_adaptive_xp_formal_target10_${XP_PACKAGE_VERSION}.lock

exec 9>"${XP_LOCK}"
if ! flock -n 9; then
    printf '%s\n' \
        '{"status":"blocked_duplicate_controller","lock":"/tmp/resource_adaptive_xp_formal_target10_v20.lock"}' >&2
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
    "${XP_EXPECTED_PACKAGE_SHA}"
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
test "$(sha256sum "${XP_BOOTSTRAP_SELECTION}" | awk '{print $1}')" = \
    deba97e4211b1b9a2ae134f2ad26e3c3aa9654e49456c16e2dba2367ab7469cf
test "$(sha256sum "${XP_BOOTSTRAP_REPLAY}" | awk '{print $1}')" = \
    bddb68c61e097634238952ce64265a11c396a29b358f57fc68a2bf50f0206649

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
    --replay-audit "${XP_BOOTSTRAP_REPLAY}" \
    --rank1-evidence "${XP_BOOTSTRAP_SELECTION}" \
    --restore-script "${XP_EXPERIENCE}/restore_with_physical_io_reapply_attempt25.sh"
