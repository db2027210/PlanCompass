#!/usr/bin/env bash
set -euo pipefail

parent_pid=2014482
child_pid=3369007
expected_parent=tpcc_final_fresh0_autonomous_v42_20260801/tpcc_autonomous_target10_controller_v25.py
expected_child=autonomous_diagnostic_validation_76/top5.json
audit=/tmp/tpcc_v42_clean_boundary_stop_20260801.audit

parent_cmd=$(tr '\0' ' ' < "/proc/${parent_pid}/cmdline")
child_cmd=$(tr '\0' ' ' < "/proc/${child_pid}/cmdline")
case "${parent_cmd}" in
  *"${expected_parent}"*) ;;
  *) echo "parent command mismatch" >&2; exit 1 ;;
esac
case "${child_cmd}" in
  *"${expected_child}"*) ;;
  *) echo "child command mismatch" >&2; exit 1 ;;
esac

while test -e "/proc/${child_pid}"; do
  sleep 1
done

test -e "/proc/${parent_pid}"
parent_cmd=$(tr '\0' ' ' < "/proc/${parent_pid}/cmdline")
case "${parent_cmd}" in
  *"${expected_parent}"*) ;;
  *) echo "parent command changed before boundary" >&2; exit 1 ;;
esac

kill -KILL "${parent_pid}"
{
  date '+stopped_at=%F %T %Z'
  echo "parent_pid=${parent_pid}"
  echo "completed_child_pid=${child_pid}"
  echo "boundary=child_exited_before_parent_next_iteration"
} > "${audit}"
