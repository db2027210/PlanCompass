#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
ATTEMPT=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726
RUN=${ATTEMPT}/autonomous_target10_controller_v4/runs/autonomous_diagnostic_45
SUMMARY=${RUN}/top5_real_replay_round12_diagnostic_v1/summary.json
LEDGER=${RUN}/strict_top5_real_replay_round12_ledger_v1.json
FORMAL=${ATTEMPT}/formal10_tpcc_state.json
STATE=${PROJECT}/history/tpcc_autonomous_target10_controller_v12_20260731/controller_state.json
BARRIER=${PROJECT}/history/tpcc_autonomous_target10_controller_v12_20260731/prepare_46
ARCHIVE=${PROJECT}/history/tpcc_autonomous_target10_controller_v12_20260731/prepare_46_handover_barrier_v22_20260731T1725
PACKAGE=/tmp/tpcc_autonomous_target10_package_v23_20260731
PACKAGE_MANIFEST=${PACKAGE}/package_manifest.json
LAUNCHER=${PACKAGE}/launch_armed_foreground.sh
LOG=${PROJECT}/history/tpcc_autonomous_target10_controller_v12_20260731/handover_v23.log
LOCK=${PROJECT}/history/tpcc_autonomous_target10_controller_v12_20260731/handover_v23.lock
OLD_CONTROLLER_PID=2282731
BATCH_PID=2793143

EXPECTED_FORMAL_SHA=f8f16a0c8ed261e2158fa5c54f061ebb4ce3aa45e6eab816c9db40bf40ab588a
EXPECTED_PACKAGE_MANIFEST_SHA=0787bdb2f19761bfbdee2a9cc4f0a3b864d0324bd3f8e555ca55bb49e54551df
EXPECTED_LAUNCHER_SHA=738f2c2f6fab07498d13748e03af480c88f291683b2bc9fe9bbb12ac221455a7
EXPECTED_CONTROLLER_SHA=692bf7196678a455867e2bc9db05009c242d0ac66498734cab59500def23c8ba

exec 9>"${LOCK}"
if ! flock -n 9; then
    echo "TPC-C v23 handover watcher lock is already held" >&2
    exit 73
fi
exec >>"${LOG}" 2>&1

test "$(sha256sum "${FORMAL}" | awk '{print $1}')" = "${EXPECTED_FORMAL_SHA}"
test "$(sha256sum "${PACKAGE_MANIFEST}" | awk '{print $1}')" = \
    "${EXPECTED_PACKAGE_MANIFEST_SHA}"
test "$(sha256sum "${LAUNCHER}" | awk '{print $1}')" = \
    "${EXPECTED_LAUNCHER_SHA}"
test "$(jq -r '.controller_sha256' "${PACKAGE_MANIFEST}")" = \
    "${EXPECTED_CONTROLLER_SHA}"
test -f "${BARRIER}/handover_barrier.json"
test ! -e "${ARCHIVE}"

for _ in $(seq 1 720); do
    if ! kill -0 "${BATCH_PID}" 2>/dev/null; then
        break
    fi
    BATCH_COMMAND="$(ps -p "${BATCH_PID}" -o args=)"
    case "${BATCH_COMMAND}" in
        *autonomous_diagnostic_45/top5_real_replay_round12_diagnostic_v1*)
            ;;
        *)
            echo "TPC-C batch45 PID command mismatch" >&2
            exit 74
            ;;
    esac
    sleep 10
done
if kill -0 "${BATCH_PID}" 2>/dev/null; then
    echo "TPC-C batch45 exceeded handover wait limit" >&2
    exit 75
fi

test -f "${SUMMARY}"
test -f "${LEDGER}"
jq -e '
    .real_workload_invocation_count == 5 and
    .successful_real_replay_count == 5 and
    .technical_failure_count == 0 and
    .baseline_params_restored == true and
    .baseline_snapshot_restored == true and
    .full_top5_complete == true and
    .round_complete == true and
    .fatal_error == null and
    .adaptive_real_replay_enabled == false and
    .state_commit_eligible == false and
    .accepted_count_delta == 0 and
    .incumbent_updated == false and
    .range_updated == false and
    .gradient_update_applied == false and
    .rejected_candidates_update_state == false
' "${SUMMARY}" >/dev/null
test "$(sha256sum "${FORMAL}" | awk '{print $1}')" = "${EXPECTED_FORMAL_SHA}"

for _ in $(seq 1 60); do
    if ! kill -0 "${OLD_CONTROLLER_PID}" 2>/dev/null; then
        break
    fi
    sleep 5
done
if kill -0 "${OLD_CONTROLLER_PID}" 2>/dev/null; then
    echo "TPC-C v22 controller did not stop at handover barrier" >&2
    exit 76
fi

jq -e --arg root "${RUN}" '
    .current_run == null and
    .next_ordinal == 46 and
    .audits[-1].root == $root and
    .audits[-1].mode == "diagnostic" and
    .audits[-1].clean == true and
    .audits[-1].physical_calls == 5 and
    .audits[-1].valid_replays == 5 and
    .audits[-1].technical_failures == 0
' "${STATE}" >/dev/null
test "$(sha256sum "${FORMAL}" | awk '{print $1}')" = "${EXPECTED_FORMAL_SHA}"

mv "${BARRIER}" "${ARCHIVE}"
test -f "${ARCHIVE}/handover_barrier.json"
exec "${LAUNCHER}"
