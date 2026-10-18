#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
ATTEMPT=${LANE_ROOT}/history/job/outer/attempt_7_cap5_contract_20260726
FORMAL=${ATTEMPT}/formal10_after_round10_gate_20260729T1055
WORKSPACE=${LANE_ROOT}/workspace_attempt7
PYTHON=/home/anonymous/anaconda3/bin/python
PYTHON_RUNTIME_SITE=${LANE_ROOT}/runtime/python/lib/python3.11/site-packages

test -f "${FORMAL}/config.json"
test "$(cat "${FORMAL}/state/formal_search_accepted_counter.txt")" = "0"
test "$(cat "${ATTEMPT}/formal_search_replay_counter.txt")" = "130"
test "$(cat "${ATTEMPT}/replay_counter.txt")" = "189"

export RA_PG_JAVA_HOME="${RA_PG_JAVA_HOME:-/mnt/nvme2n1/anonymous_resource_adaptive/jdk/jdk-23.0.2-oracle-x64}"
export JAVA_HOME="${RA_PG_JAVA_HOME}"
export PATH="${JAVA_HOME}/bin:${PATH}"
export PATH="/mnt/nvme0n1/ogtest/postgres/bin:/home/anonymous/anaconda3/bin:${PATH}"
export PSQL_BIN=/mnt/nvme0n1/ogtest/postgres/bin/psql
export PYTHONPATH="${PYTHON_RUNTIME_SITE}:${WORKSPACE}"
export PYTHONNOUSERSITE=1
export RA_PG_FROZEN_ROOT="${LANE_ROOT}/bundle/attempt8_frozen"
export RA_PG_TEMPLATE_ROOT="${LANE_ROOT}/template"
export RA_PG_MOUNT_ROOT="/mnt/nvme2n1"
export RA_PG_IO_DEVICE_SOURCE="/dev/nvme2n1"
export RA_PG_UNPRIVILEGED_RUNTIME=1
export RA_PG_SKIP_DIRECT_CGROUP_WRITE=1
export RA_PG_JDBC_JAR="${LANE_ROOT}/assets/postgresql-42.7.4.jar"
export RA_PG_JOB_SQL="${LANE_ROOT}/assets/job_sql/26a.sql"
export RA_PG_CPUSET="12-17,48-53"
export RA_PG_CPUSET_MEMS="0"
export RA_PG_CPU_CORES="3.0"
export RA_PG_WORK_ROOT="${LANE_ROOT}/runtime"
export RA_PG_RANDOM_WALK_RUNTIME_ROOT="${LANE_ROOT}/runtime/random_walk"
export RA_PG_RANDOM_WALK_TEMPLATE_ROOT="${LANE_ROOT}/template"
export RA_PG_OUTPUT_ROOT="${ATTEMPT}"
export RA_PG_FORMAL_SEARCH_MAX_REPLAYS=260
export RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP=260
export OPTIMIZER_CONFIG="${FORMAL}/config.json"
export FINAL_FEEDBACK_MAX_ROUNDS=1
export ESTIMATOR_BASELINE_BINDING_PATH="${ATTEMPT}/estimator_baseline_binding_memory_v1.json"
export ESTIMATOR_BASELINE_BINDING_SHA256="$(sha256sum "${ESTIMATOR_BASELINE_BINDING_PATH}" | awk '{print $1}')"
export PGHOST=127.0.0.1 PGPORT=64309 PGUSER=postgres PGPASSWORD=CHANGE_ME PG_DBNAME=imdb_job
export BONECT_OPTIMIZER_GLOBAL_LOCK=1
export BONECT_OPTIMIZER_LOCK_PATH="${LANE_ROOT}/lease/job_formal10_after_round10.lock"

cd "${WORKSPACE}"
exec "${PYTHON}" -u parameter_optimizer.py
