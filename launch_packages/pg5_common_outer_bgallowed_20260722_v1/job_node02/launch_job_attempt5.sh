#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
WORKSPACE=${LANE_ROOT}/workspace_attempt5
PYTHON=${LANE_ROOT}/runtime/python/bin/python
FROZEN=${LANE_ROOT}/bundle/attempt5_frozen

export PATH="${LANE_ROOT}/runtime/python/bin:/mnt/nvme2n1/anonymous_resource_adaptive/jdk/jdk-23.0.2-oracle-x64/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONPATH="${WORKSPACE}"
export PYTHONNOUSERSITE=1
export TMPDIR="${LANE_ROOT}/tmp/host_attempt5"
export XDG_CACHE_HOME="${LANE_ROOT}/tmp/host_attempt5/xdg-cache"
export JAVA_TOOL_OPTIONS="-Djava.io.tmpdir=${LANE_ROOT}/tmp/host_attempt5/java"
export RA_PG_CAMPAIGN_ID=pg5_common_outer_bgallowed_20260722_v1
export RA_PG_OUTPUT_ROOT="${LANE_ROOT}/history"
export RA_PG_FROZEN_ROOT="${FROZEN}"
export RA_PG_TEMPLATE_ROOT="${LANE_ROOT}/template"
export RA_PG_WORK_ROOT="${LANE_ROOT}/work/runtime_attempt5"
export RA_PG_MOUNT_ROOT=/mnt/nvme2n1
export RA_PG_IO_DEVICE_SOURCE=/dev/nvme2n1
export RA_PG_CONTAINER_SUFFIX=pg5jobnode02v1a5
export RA_PG_PORT_OFFSET=8003
export RA_PG_CPUSET=12-17,48-53
export RA_PG_CPUSET_MEMS=0
export RA_PG_CPU_CORES=1.5
export RA_PG_MEMORY_BYTES=3221225472
export RA_PG_SHARED_BUFFERS=2GB
export RA_PG_EFFECTIVE_CACHE_SIZE=3GB
export RA_PG_IO_BPS=134217728
export RA_PG_PHASE_HARD_TIMEOUT_SECONDS=600
export RA_PG_RUN_ATTEMPT_ID=shared_baseline_R0_attempt_5
export RA_PG_EXPECTED_IMAGE_ID=sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458
export RA_PG_IMAGE=postgres:17
export RA_PG_JDBC_JAR="${LANE_ROOT}/assets/postgresql-42.7.4.jar"
export RA_PG_JOB_SQL="${LANE_ROOT}/assets/job_sql/26a.sql"

mkdir -p "${TMPDIR}/java" "${XDG_CACHE_HOME}"
install -d -m 0777 "${LANE_ROOT}/tmp/postgres_attempt5" "${LANE_ROOT}/socket/postgresql_attempt5"
printf '%s\n' "$$" >"${LANE_ROOT}/lease/baseline_R0_attempt5.pid"
(cd "${FROZEN}" && sha256sum -c checksums.sha256)

mpstat -P ALL 1 >"${LANE_ROOT}/background/attempt5_runtime_mpstat.log" 2>&1 &
MPSTAT_PID=$!
iostat -dx 1 >"${LANE_ROOT}/background/attempt5_runtime_iostat.log" 2>&1 &
IOSTAT_PID=$!
vmstat 1 >"${LANE_ROOT}/background/attempt5_runtime_vmstat.log" 2>&1 &
VMSTAT_PID=$!
cleanup_monitors() {
    kill "${MPSTAT_PID}" "${IOSTAT_PID}" "${VMSTAT_PID}" 2>/dev/null || true
}
trap cleanup_monitors EXIT INT TERM

cd "${WORKSPACE}"
"${PYTHON}" -u "${LANE_ROOT}/launch/job_lane_baseline_attempt5.py"
