#!/usr/bin/env bash
set -u

ATTEMPT="/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
FORMAL="${ATTEMPT}/formal10_after_round10_gate_cont7_feedback_v1_20260729T1645"
OPTIMIZER_PID="109437"
POINT_RESULT="${ATTEMPT}/formal_points/point_0208/point_result.json"
REPLAY_COUNTER="${ATTEMPT}/formal_search_replay_counter.txt"
GLOBAL_COUNTER="${ATTEMPT}/replay_counter.txt"
LOG="${FORMAL}/cap5_watchdog.log"

printf 'watchdog_start=%s\n' "$(date -Is)" >>"${LOG}"
if ! test "$(cat "${REPLAY_COUNTER}")" = "149" \
    || ! test "$(cat "${GLOBAL_COUNTER}")" = "208" \
    || ! kill -0 "${OPTIMIZER_PID}" 2>/dev/null; then
    printf 'fail_closed_precondition=%s\n' "$(date -Is)" >>"${LOG}"
    exit 97
fi

for _index in $(seq 1 450); do
    if test -f "${POINT_RESULT}"; then
        printf 'point_0208_complete=%s\n' "$(date -Is)" >>"${LOG}"
        sleep 2
        kill -TERM "${OPTIMIZER_PID}" 2>/dev/null || true
        kill -CONT "${OPTIMIZER_PID}" 2>/dev/null || true
        printf 'optimizer_term_pending_then_cont=%s\n' "$(date -Is)" >>"${LOG}"
        exit 0
    fi
    if ! kill -0 "${OPTIMIZER_PID}" 2>/dev/null; then
        printf 'optimizer_exited_before_point_result=%s\n' "$(date -Is)" >>"${LOG}"
        exit 98
    fi
    sleep 2
done

printf 'point_0208_timeout=%s\n' "$(date -Is)" >>"${LOG}"
kill -TERM "${OPTIMIZER_PID}" 2>/dev/null || true
kill -CONT "${OPTIMIZER_PID}" 2>/dev/null || true
exit 99
