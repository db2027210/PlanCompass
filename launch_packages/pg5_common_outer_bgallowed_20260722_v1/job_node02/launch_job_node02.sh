#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
WORKSPACE=${LANE_ROOT}/workspace
PYTHON=${LANE_ROOT}/runtime/python/bin/python

export PATH="${LANE_ROOT}/runtime/python/bin:/mnt/nvme2n1/anonymous_resource_adaptive/jdk/jdk-23.0.2-oracle-x64/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONPATH="${WORKSPACE}"
export PYTHONNOUSERSITE=1
export TMPDIR="${LANE_ROOT}/tmp/host"
export XDG_CACHE_HOME="${LANE_ROOT}/tmp/host/xdg-cache"
export JAVA_TOOL_OPTIONS="-Djava.io.tmpdir=${LANE_ROOT}/tmp/host/java"
export RA_PG_CAMPAIGN_ID=pg5_common_outer_bgallowed_20260722_v1
export RA_PG_OUTPUT_ROOT="${LANE_ROOT}/history"
export RA_PG_FROZEN_ROOT="${LANE_ROOT}/bundle/frozen"
export RA_PG_TEMPLATE_ROOT="${LANE_ROOT}/template"
export RA_PG_WORK_ROOT="${LANE_ROOT}/work/runtime"
export RA_PG_MOUNT_ROOT=/mnt/nvme2n1
export RA_PG_IO_DEVICE_SOURCE=/dev/nvme2n1
export RA_PG_CONTAINER_SUFFIX=pg5jobnode02v1
export RA_PG_PORT_OFFSET=8000
export RA_PG_CPUSET=12-17,48-53
export RA_PG_CPUSET_MEMS=0
export RA_PG_CPU_CORES=3
export RA_PG_MEMORY_BYTES=6442450944
export RA_PG_IO_BPS=1205862400
export RA_PG_PHASE_HARD_TIMEOUT_SECONDS=600
export RA_PG_RUN_ATTEMPT_ID=shared_baseline_R0_attempt_1
export RA_PG_EXPECTED_IMAGE_ID=sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458
export RA_PG_IMAGE=postgres:17
export RA_PG_JDBC_JAR="${LANE_ROOT}/assets/postgresql-42.7.4.jar"
export RA_PG_JOB_SQL="${LANE_ROOT}/assets/job_sql/26a.sql"

mkdir -p "${TMPDIR}/java" "${XDG_CACHE_HOME}"
printf '%s\n' "$$" >"${LANE_ROOT}/lease/baseline_R0.pid"
"${PYTHON}" "${LANE_ROOT}/launch/prepare_lane.py" >>"${LANE_ROOT}/logs/prepare_lane.log" 2>&1

mpstat -P ALL 1 >"${LANE_ROOT}/background/runtime_mpstat.log" 2>&1 &
MPSTAT_PID=$!
iostat -dx 1 >"${LANE_ROOT}/background/runtime_iostat.log" 2>&1 &
IOSTAT_PID=$!
vmstat 1 >"${LANE_ROOT}/background/runtime_vmstat.log" 2>&1 &
VMSTAT_PID=$!
cleanup_monitors() {
    kill "${MPSTAT_PID}" "${IOSTAT_PID}" "${VMSTAT_PID}" 2>/dev/null || true
}
trap cleanup_monitors EXIT INT TERM

cd "${WORKSPACE}"
"${PYTHON}" -u "${LANE_ROOT}/launch/job_lane_baseline.py"
