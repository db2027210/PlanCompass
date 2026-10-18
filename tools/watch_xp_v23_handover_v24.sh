#!/usr/bin/env bash
set -euo pipefail

# compute00-local handover.  v23 is allowed to finish cont004 and cont005.  A
# regular-file barrier makes its first cont006 generation fail closed before
# any workload.  The watcher then proves formal state neutrality, archives the
# barrier, and execs the sealed v24 controller.

PROJECT=/home/anonymous/program/resource_adaptive
ROOT=${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731
STATE=${ROOT}/state.json
BATCHES=${ROOT}/batches
CONTROLLER_PID=2793238
BARRIER=${BATCHES}/cont006/explain_only
ARCHIVE=${BATCHES}/cont006/explain_only_handover_barrier_v23_20260731T1750
LAUNCHER=${PROJECT}/tools/launch_xp_autonomous_formal_target10_v24.sh
BASE_LAUNCHER=${PROJECT}/tools/launch_xp_autonomous_formal_target10_v20.sh
MANIFEST=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v7_entry_gate_only/bundle_manifest_v24_safe_frontier.json
FORMAL_COUNTER=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726/formal10_cont16b_accepted4_skip_initial_v1_20260729T2245/state/formal_search_accepted_counter.txt
LOCK=${ROOT}/handover_v24.lock
LOG=${ROOT}/handover_v24.log

EXPECTED_BARRIER_SHA=b3484536d52033e2a05b8962336d757ca23d97f2a0ea4390fee678c3b4703200
EXPECTED_STATE_SUBSET_SHA=d45b2885bcb3c111dac4203b6a93d2cb6c679869082c34fe3cc26037e4e12285
EXPECTED_LAUNCHER_SHA=34c953e6435c16c0c85a60edd1182e08fe4ea928c267db336b5eaa4b686e9fc9
EXPECTED_BASE_LAUNCHER_SHA=42e0dcdae3bfbe4a6a72ccae93716d5ca7dff57744e5a3e653d831aa2aa173ec
EXPECTED_MANIFEST_SHA=473cb2471857490d7aced85597f5e86b87558d46dcd4ff9b7e957b1ab44a8c10

exec 9>"${LOCK}"
if ! flock -n 9; then
    echo "XP v24 handover watcher lock is already held" >&2
    exit 73
fi
exec >>"${LOG}" 2>&1

test "$(sha256sum "${BARRIER}" | awk '{print $1}')" = "${EXPECTED_BARRIER_SHA}"
test ! -e "${ARCHIVE}"
test "$(sha256sum "${LAUNCHER}" | awk '{print $1}')" = "${EXPECTED_LAUNCHER_SHA}"
test "$(sha256sum "${BASE_LAUNCHER}" | awk '{print $1}')" = "${EXPECTED_BASE_LAUNCHER_SHA}"
test "$(sha256sum "${MANIFEST}" | awk '{print $1}')" = "${EXPECTED_MANIFEST_SHA}"
test "$(tr -d '[:space:]' < "${FORMAL_COUNTER}")" = "4"

for _ in $(seq 1 1080); do
    if ! kill -0 "${CONTROLLER_PID}" 2>/dev/null; then
        break
    fi
    COMMAND="$(ps -p "${CONTROLLER_PID}" -o args=)"
    case "${COMMAND}" in
        *bundle_fix_v6/xp_unattended_launch.py*) ;;
        *) echo "XP v23 PID command mismatch" >&2; exit 74 ;;
    esac
    sleep 10
done
if kill -0 "${CONTROLLER_PID}" 2>/dev/null; then
    echo "XP v23 exceeded handover wait limit" >&2
    exit 75
fi

# The old process may have completed both scheduled diagnostic batches, or it
# may have failed closed earlier during candidate generation.  In either case
# no accepted state is permitted to change before v24 starts.
OBSERVED_SUBSET_SHA="$(
    jq -cS '{accepted_count,incumbent_score,incumbent_params,parameter_ranges,range_state}' "${STATE}" |
        sha256sum | awk '{print $1}'
)"
test "${OBSERVED_SUBSET_SHA}" = "${EXPECTED_STATE_SUBSET_SHA}"
test "$(tr -d '[:space:]' < "${FORMAL_COUNTER}")" = "4"
jq -e '
    .accepted_count == 4 and
    .accepted_target == 10 and
    .status == "blocked_contract" and
    (.next_batch_index == 5 or .next_batch_index == 6) and
    (.last_error | type == "string")
' "${STATE}" >/dev/null

for batch in cont004 cont005; do
    if test -f "${BATCHES}/${batch}/replay_summary.json"; then
        jq -e '
            .batch_mode == "diagnostic_validation" and
            .diagnostic_only == true and
            .physical_workload_invocation_count == 5 and
            .successful_real_replay_count == 5 and
            .technical_failure_count == 0 and
            .baseline_restore_verified == true and
            .accepted_count_delta == 0 and
            .incumbent_updated == false and
            .range_updated == false and
            .gradient_update_applied == false
        ' "${BATCHES}/${batch}/replay_summary.json" >/dev/null
    fi
done

# If cont005 completed, v23 must have reached the deliberate zero-workload
# cont006 barrier.  If it failed closed at cont005 generation, the barrier is
# still harmless and v24 resumes cont005.
test ! -e "${BATCHES}/cont006/serial_replay"
mv "${BARRIER}" "${ARCHIVE}"
test "$(sha256sum "${ARCHIVE}" | awk '{print $1}')" = "${EXPECTED_BARRIER_SHA}"
chmod +x "${LAUNCHER}" "${BASE_LAUNCHER}"
exec "${LAUNCHER}"
