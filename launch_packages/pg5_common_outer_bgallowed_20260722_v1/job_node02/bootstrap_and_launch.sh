#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
printf '%s\n' "$$" >"${LANE_ROOT}/lease/bootstrap.pid"
mpstat -P ALL 1 >"${LANE_ROOT}/background/prelaunch_mpstat.log" 2>&1 &
MPSTAT_PID=$!
iostat -dx 1 >"${LANE_ROOT}/background/prelaunch_iostat.log" 2>&1 &
IOSTAT_PID=$!
vmstat 1 >"${LANE_ROOT}/background/prelaunch_vmstat.log" 2>&1 &
VMSTAT_PID=$!
cleanup_monitors() {
    kill "${MPSTAT_PID}" "${IOSTAT_PID}" "${VMSTAT_PID}" 2>/dev/null || true
}
trap cleanup_monitors EXIT INT TERM
"${LANE_ROOT}/launch/build_job_template.sh"
cleanup_monitors
trap - EXIT INT TERM
exec "${LANE_ROOT}/launch/launch_job_node02.sh" >>"${LANE_ROOT}/logs/baseline_R0.log" 2>&1
