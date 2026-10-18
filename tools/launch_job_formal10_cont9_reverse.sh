#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
ATTEMPT=${LANE_ROOT}/history/job/outer/attempt_7_cap5_contract_20260726
FORMAL=${ATTEMPT}/formal10_after_round10_gate_cont13_ratio_anchor_reuse_v1_20260729T2005
WORKSPACE=${LANE_ROOT}/workspace_attempt7
PYTHON=/home/anonymous/anaconda3/bin/python
PYTHON_RUNTIME_SITE=${LANE_ROOT}/runtime/python/lib/python3.11/site-packages
PSQL=/mnt/nvme0n1/ogtest/postgres/bin/psql
ACCEPTED_COUNTER=${ATTEMPT}/formal10_accepted_counter.txt
PATCHED_OPTIMIZER_SHA=c54b9dba238bb03c5333c0dbbb780299f0ecaf09de2d8de3c590ba693c1c0ed2
accepted_before=

restore_rejected_baseline() {
    local accepted_after assignment
    accepted_after="$(cat "${ACCEPTED_COUNTER}")"
    if [[ "${accepted_after}" != "${accepted_before}" ]]; then
        return 0
    fi
    for assignment in \
        seq_page_cost=1 random_page_cost=4 cpu_tuple_cost=0.01 \
        cpu_index_tuple_cost=0.0005 cpu_operator_cost=0.0025 \
        parallel_setup_cost=7000 parallel_tuple_cost=0.001
    do
        PGPASSWORD=CHANGE_ME "${PSQL}" -X -v ON_ERROR_STOP=1 \
            -h 127.0.0.1 -p 64309 -U postgres -d imdb_job \
            -c "ALTER SYSTEM SET ${assignment%%=*} = '${assignment#*=}'" >/dev/null
    done
    PGPASSWORD=CHANGE_ME "${PSQL}" -X -v ON_ERROR_STOP=1 \
        -h 127.0.0.1 -p 64309 -U postgres -d imdb_job \
        -c "SELECT pg_reload_conf()" >/dev/null
    PGPASSWORD=CHANGE_ME "${PSQL}" -X -A -t -F = \
        -h 127.0.0.1 -p 64309 -U postgres -d imdb_job \
        -c "SELECT name,setting FROM pg_settings WHERE name IN ('seq_page_cost','random_page_cost','cpu_tuple_cost','cpu_index_tuple_cost','cpu_operator_cost','parallel_setup_cost','parallel_tuple_cost') ORDER BY name" \
        >"${FORMAL}/rejected_baseline_restore_readback.txt"
}

finalize() {
    local optimizer_rc=$? restore_rc=0
    trap - EXIT
    restore_rejected_baseline || restore_rc=$?
    if [[ "${optimizer_rc}" -eq 0 && "${restore_rc}" -ne 0 ]]; then
        optimizer_rc=97
    fi
    printf '%s\n' "${optimizer_rc}" >"${FORMAL}/driver_exit_code.txt"
    exit "${optimizer_rc}"
}
trap finalize EXIT

test -f "${FORMAL}/config.json"
test "$(cat "${ACCEPTED_COUNTER}")" = "0"
test "$(cat "${ATTEMPT}/formal_search_replay_counter.txt")" = "156"
test "$(cat "${ATTEMPT}/replay_counter.txt")" = "215"
test "$(sha256sum "${WORKSPACE}/parameter_optimizer.py" | awk '{print $1}')" = \
    "${PATCHED_OPTIMIZER_SHA}"
test "$(sha256sum "${FORMAL}/config.json" | awk '{print $1}')" = \
    "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["output_config_sha256"])' "${FORMAL}/continuation_manifest.json")"
if pgrep -af '[p]arameter_optimizer.py' | grep -F "${WORKSPACE}" >/dev/null; then
    echo JOB_OPTIMIZER_ALREADY_ACTIVE >&2
    exit 73
fi
accepted_before="$(cat "${ACCEPTED_COUNTER}")"

export RA_PG_JAVA_HOME="${RA_PG_JAVA_HOME:-/mnt/nvme2n1/anonymous_resource_adaptive/jdk/jdk-23.0.2-oracle-x64}"
export JAVA_HOME="${RA_PG_JAVA_HOME}"
export PATH="${JAVA_HOME}/bin:/mnt/nvme0n1/ogtest/postgres/bin:/home/anonymous/anaconda3/bin:${PATH}"
export PSQL_BIN="${PSQL}"
export PYTHONPATH="${PYTHON_RUNTIME_SITE}:${WORKSPACE}"
export PYTHONNOUSERSITE=1
export RA_PG_FROZEN_ROOT="${LANE_ROOT}/bundle/attempt8_frozen"
export RA_PG_TEMPLATE_ROOT="${LANE_ROOT}/template"
export RA_PG_MOUNT_ROOT=/mnt/nvme2n1
export RA_PG_IO_DEVICE_SOURCE=/dev/nvme2n1
export RA_PG_UNPRIVILEGED_RUNTIME=1
export RA_PG_SKIP_DIRECT_CGROUP_WRITE=1
export RA_PG_JDBC_JAR="${LANE_ROOT}/assets/postgresql-42.7.4.jar"
export RA_PG_JOB_SQL="${LANE_ROOT}/assets/job_sql/26a.sql"
export RA_PG_CPUSET=12-17,48-53
export RA_PG_CPUSET_MEMS=0
export RA_PG_CPU_CORES=3.0
export RA_PG_WORK_ROOT="${LANE_ROOT}/runtime"
export RA_PG_RANDOM_WALK_RUNTIME_ROOT="${LANE_ROOT}/runtime/random_walk"
export RA_PG_RANDOM_WALK_TEMPLATE_ROOT="${LANE_ROOT}/template"
export RA_PG_OUTPUT_ROOT="${ATTEMPT}"
export RA_PG_FORMAL_SEARCH_MAX_REPLAYS=161
export RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP=161
export OPTIMIZER_CONFIG="${FORMAL}/config.json"
export FINAL_FEEDBACK_LOOP_ENABLED=1
export FINAL_FEEDBACK_MAX_ROUNDS=1
export RESOURCE_MODEL_RETRAIN_ENABLED=0
export BONECT_STRICT_RESOURCE_RETRAIN_GATE=1
export BONECT_ALLOW_V5_RETRAIN_FALLBACK=0
export ESTIMATOR_BASELINE_BINDING_PATH="${ATTEMPT}/estimator_baseline_binding_memory_v1.json"
export ESTIMATOR_BASELINE_BINDING_SHA256="$(sha256sum "${ESTIMATOR_BASELINE_BINDING_PATH}" | awk '{print $1}')"
export PGHOST=127.0.0.1 PGPORT=64309 PGUSER=postgres PGPASSWORD=CHANGE_ME PG_DBNAME=imdb_job
export BONECT_OPTIMIZER_GLOBAL_LOCK=1
export BONECT_OPTIMIZER_LOCK_PATH="${LANE_ROOT}/lease/job_formal10_after_round10.lock"

cd "${WORKSPACE}"
"${PYTHON}" -u parameter_optimizer.py
