#!/usr/bin/env bash
set -euo pipefail

LANE=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
TOOLS="$LANE/validation_tools/job_ratio_scale_canary_20260725"
WORKSPACE="$LANE/workspace_attempt6"
OUTPUT="$LANE/history/job/canary/job_ratio_scale_direction_v1_20260725"
PYTHON="$LANE/runtime/python/bin/python"
LOG="$TOOLS/canary_run.log"
FROZEN="$LANE/bundle/attempt6_frozen"

export PATH="$LANE/runtime/python/bin:/mnt/nvme2n1/anonymous_resource_adaptive/jdk/jdk-23.0.2-oracle-x64/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONNOUSERSITE=1
export TMPDIR="$LANE/tmp/host_attempt6"
export XDG_CACHE_HOME="$LANE/tmp/host_attempt6/xdg-cache"
export JAVA_TOOL_OPTIONS="-Djava.io.tmpdir=$LANE/tmp/host_attempt6/java"
export RA_PG_CAMPAIGN_ID=pg5_common_outer_bgallowed_20260722_v1
export RA_PG_OUTPUT_ROOT="$LANE/history"
export RA_PG_FROZEN_ROOT="$FROZEN"
export RA_PG_TEMPLATE_ROOT="$LANE/template"
export RA_PG_WORK_ROOT="$LANE/work/runtime_attempt6"
export RA_PG_MOUNT_ROOT=/mnt/nvme2n1
export RA_PG_IO_DEVICE_SOURCE=/dev/nvme2n1
export RA_PG_CONTAINER_SUFFIX=pg5jobnode02v1a6
export RA_PG_PORT_OFFSET=8004
export RA_PG_CPUSET=12-17,48-53
export RA_PG_CPUSET_MEMS=0
export RA_PG_CPU_CORES=1.5
export RA_PG_MEMORY_BYTES=3221225472
export RA_PG_SHARED_BUFFERS=2GB
export RA_PG_EFFECTIVE_CACHE_SIZE=3GB
export RA_PG_IO_BPS=134217728
export RA_PG_PHASE_HARD_TIMEOUT_SECONDS=600
export RA_PG_RUN_ATTEMPT_ID=job_ratio_scale_direction_v1_20260725
export RA_PG_EXPECTED_IMAGE_ID=sha256:6c2f8e0ad2949b4e100456f138b1184fe58cb2010cd7a9721008abb87f501458
export RA_PG_IMAGE=postgres:17
export RA_PG_JDBC_JAR="$LANE/assets/postgresql-42.7.4.jar"
export RA_PG_JOB_SQL="$LANE/assets/job_sql/26a.sql"

cd "$WORKSPACE"
export PYTHONPATH="$WORKSPACE"
: >"$LOG"
exec "$PYTHON" "$TOOLS/run_job_ratio_canary.py" \
  --output-root "$OUTPUT" \
  --estimator-evidence "$TOOLS/estimator_comparison.json" \
  >>"$LOG" 2>&1
