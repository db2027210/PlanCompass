#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
WORKSPACE=${LANE_ROOT}/workspace_outer
OUTPUT=${LANE_ROOT}/history/job/outer/attempt_1
CONFIG=${OUTPUT}/generated_config/formal_job_outer_attempt1/memory/job.json
VENV_SITE=${LANE_ROOT}/runtime/python/lib/python3.11/site-packages
PYTHON_BIN=/home/anonymous/anaconda3/bin/python3

export LC_ALL=C
export PATH="/mnt/nvme0n1/ogtest/postgres/bin:/home/anonymous/anaconda3/bin:/mnt/nvme2n1/anonymous_resource_adaptive/jdk/jdk-23.0.2-oracle-x64/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONPATH="${WORKSPACE}:${VENV_SITE}"
export PYTHONNOUSERSITE=1
export OPTIMIZER_CONFIG="${CONFIG}"
export METRICS_SOURCE=plan-estimator
export PGHOST=127.0.0.1
export PGPORT=64309
export PGUSER=postgres
export PGPASSWORD=CHANGE_ME
export PG_DBNAME=imdb_job
export BONECT_OPTIMIZER_GLOBAL_LOCK=1
export BONECT_OPTIMIZER_LOCK_PATH="${LANE_ROOT}/lease/job_outer_parameter_optimizer.lock"

export RA_PG_CAMPAIGN_ID=pg5_common_outer_bgallowed_20260722_v1
export RA_PG_OUTPUT_ROOT="${OUTPUT}"
export RA_PG_FROZEN_ROOT="${LANE_ROOT}/bundle/attempt8_frozen"
export RA_PG_TEMPLATE_ROOT="${LANE_ROOT}/template"
export RA_PG_WORK_ROOT="${LANE_ROOT}/work/runtime_outer"
export RA_PG_MOUNT_ROOT=/mnt/nvme2n1
export RA_PG_IO_DEVICE_SOURCE=/dev/nvme2n1
export RA_PG_CPUSET=12-17,48-53
export RA_PG_CPUSET_MEMS=0
export RA_PG_CPU_CORES=3.0
export RA_PG_MEMORY_BYTES=3124756480
export RA_PG_SHARED_BUFFERS=2GB
export RA_PG_EFFECTIVE_CACHE_SIZE=3GB
export RA_PG_IO_BPS=134217728
export RA_PG_PHASE_HARD_TIMEOUT_SECONDS=600
export RA_PG_RUN_ATTEMPT_ID=job_experience_table_outer_attempt1
export RA_PG_EXPECTED_IMAGE_ID=sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458
export RA_PG_IMAGE=postgres:17
export RA_PG_JDBC_JAR="${LANE_ROOT}/assets/postgresql-42.7.4.jar"
export RA_PG_JOB_SQL="${LANE_ROOT}/assets/job_sql/26a.sql"
export RA_PG_REPLAY_ACCOUNTING_SCOPE=formal_search
export RA_PG_FORMAL_SEARCH_MAX_REPLAYS=10
export RA_PG_EXCLUDE_FIRST_FORMAL_REPLAY_AS_STARTUP_BASELINE=1

mkdir -p "${OUTPUT}" "${LANE_ROOT}/work/runtime_outer" "${LANE_ROOT}/tmp/host_outer"
test "$(docker inspect --format '{{.State.Health.Status}}' ra_pg5_job_node02_v1_outercontrol)" = healthy
test -f "${CONFIG}"
"${PYTHON_BIN}" -u "${LANE_ROOT}/launch_outer/verify_job_v3_outer_preflight.py"

cleanup() {
    rc=$?
    printf '{"exit_code":%d,"completed_at":"%s"}\n' "${rc}" "$(date -Is)" >"${OUTPUT}/launcher_exit.json"
    "${LANE_ROOT}/launch_outer/cleanup_job_outer_control.sh" || true
    exit "${rc}"
}
trap cleanup EXIT

printf '%s\n' "$$" >"${LANE_ROOT}/lease/job_outer_attempt1.pid"
cd "${WORKSPACE}"
"${PYTHON_BIN}" -u parameter_optimizer.py
