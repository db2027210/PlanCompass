#!/usr/bin/env bash
set -euo pipefail

LOAD_PID="${1:?usage: $0 LOAD_PID}"
ROOT="/home/anonymous/program/resource_adaptive"
ARTIFACT_ROOT="/disk1/resource_adaptive/pg_tpch_sf5_rebuild_20260809"
LOAD_LOG="${ARTIFACT_ROOT}/worker1_load.log"
DUMP_DIR="${ARTIFACT_ROOT}/fullgrid_tpch_sf5.dumpdir"
PIPELINE_LOG="${ARTIFACT_ROOT}/finish_and_launch.log"
DB_NAME="fullgrid_tpch_sf5"
TEMPLATE_DB="template_fullgrid_tpch_sf5"
RUN_TAG="20260809_sf5_formal_v1"
PSQL_BASE="postgresql://postgres:CHANGE_ME@127.0.0.1"

exec > >(tee -a "${PIPELINE_LOG}") 2>&1

echo "[$(date --iso-8601=seconds)] waiting for BenchBase load pid=${LOAD_PID}" 
while kill -0 "${LOAD_PID}" 2>/dev/null; do
    sleep 20
done

if ! rg -q "Finished loading data into TPCH database" "${LOAD_LOG}"; then
    echo "[$(date --iso-8601=seconds)] load completion marker missing" >&2
    exit 1
fi

echo "[$(date --iso-8601=seconds)] worker1 load complete; analyzing"
psql "${PSQL_BASE}:59521/${DB_NAME}?sslmode=disable" -v ON_ERROR_STOP=1 \
    -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;" \
    -c "ANALYZE;"

echo "[$(date --iso-8601=seconds)] running authoritative worker1 Q1-Q22 smoke"
cd /home/anonymous/benchbase-postgres
java -Xmx2g -jar benchbase.jar -b tpch \
    -c "${ROOT}/config/tpch_sf5_rebuild_20260809.xml" --execute=true \
    2>&1 | tee "${ARTIFACT_ROOT}/worker1_q1_q22_smoke.log"
rg -q "Q22/22" "${ARTIFACT_ROOT}/worker1_q1_q22_smoke.log"
psql "${PSQL_BASE}:59521/${DB_NAME}?sslmode=disable" -v ON_ERROR_STOP=1 -c "CHECKPOINT;"

worker1_existing_template="$(psql "${PSQL_BASE}:59521/postgres?sslmode=disable" -Atc \
    "SELECT datname FROM pg_database WHERE datname='${TEMPLATE_DB}';")"
if [[ -n "${worker1_existing_template}" ]]; then
    echo "worker1 template already exists; refusing to overwrite: ${TEMPLATE_DB}" >&2
    exit 1
fi

psql "${PSQL_BASE}:59521/postgres?sslmode=disable" -v ON_ERROR_STOP=1 \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${DB_NAME}' AND pid<>pg_backend_pid();" \
    -c "CREATE DATABASE ${TEMPLATE_DB} WITH TEMPLATE ${DB_NAME} OWNER postgres;" \
    -c "ALTER DATABASE ${TEMPLATE_DB} WITH IS_TEMPLATE true ALLOW_CONNECTIONS false;"

if [[ -e "${DUMP_DIR}" ]]; then
    echo "dump directory already exists; refusing to overwrite: ${DUMP_DIR}" >&2
    exit 1
fi

echo "[$(date --iso-8601=seconds)] dumping worker1 database"
pg_dump -Fd -j 8 --no-owner --no-privileges \
    -f "${DUMP_DIR}" "${PSQL_BASE}:59521/${DB_NAME}?sslmode=disable"

worker2_existing="$(psql "${PSQL_BASE}:59522/postgres?sslmode=disable" -Atc \
    "SELECT datname FROM pg_database WHERE datname IN ('${DB_NAME}','${TEMPLATE_DB}') ORDER BY datname;")"
if [[ -n "${worker2_existing}" ]]; then
    echo "worker2 target names already exist; refusing to overwrite: ${worker2_existing}" >&2
    exit 1
fi

echo "[$(date --iso-8601=seconds)] restoring worker2 database"
psql "${PSQL_BASE}:59522/postgres?sslmode=disable" -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE ${DB_NAME} OWNER postgres;"
pg_restore -j 8 --no-owner --no-privileges --exit-on-error \
    -d "${PSQL_BASE}:59522/${DB_NAME}?sslmode=disable" "${DUMP_DIR}"
psql "${PSQL_BASE}:59522/${DB_NAME}?sslmode=disable" -v ON_ERROR_STOP=1 -c "ANALYZE;"
psql "${PSQL_BASE}:59522/postgres?sslmode=disable" -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE ${TEMPLATE_DB} WITH TEMPLATE ${DB_NAME} OWNER postgres;" \
    -c "ALTER DATABASE ${TEMPLATE_DB} WITH IS_TEMPLATE true ALLOW_CONNECTIONS false;"

echo "[$(date --iso-8601=seconds)] auditing both workers"
for port in 59521 59522; do
    psql "${PSQL_BASE}:${port}/${DB_NAME}?sslmode=disable" -v ON_ERROR_STOP=1 -F '|' -At \
        -c "SELECT current_database(), pg_database_size(current_database()), pg_size_pretty(pg_database_size(current_database()));" \
        -c "SELECT 'lineitem', count(*) FROM lineitem;" \
        -c "SELECT 'orders', count(*) FROM orders;" \
        -c "SELECT 'part', count(*) FROM part;" \
        > "${ARTIFACT_ROOT}/worker_${port}_audit.tsv"
    psql "${PSQL_BASE}:${port}/postgres?sslmode=disable" -v ON_ERROR_STOP=1 -F '|' -At \
        -c "SELECT datname, pg_database_size(datname), pg_size_pretty(pg_database_size(datname)) FROM pg_database WHERE datname IN ('${DB_NAME}','${TEMPLATE_DB}') ORDER BY datname;" \
        >> "${ARTIFACT_ROOT}/worker_${port}_audit.tsv"
done

diff \
    <(awk -F '|' '$1 ~ /^(lineitem|orders|part)$/ {print}' "${ARTIFACT_ROOT}/worker_59521_audit.tsv") \
    <(awk -F '|' '$1 ~ /^(lineitem|orders|part)$/ {print}' "${ARTIFACT_ROOT}/worker_59522_audit.tsv")
sha256sum "${ROOT}/config/tpch_sf5_rebuild_20260809.xml" "${DUMP_DIR}/toc.dat" \
    > "${ARTIFACT_ROOT}/sha256sums.txt"
date --iso-8601=seconds > "${ARTIFACT_ROOT}/REBUILD_COMPLETE"

echo "[$(date --iso-8601=seconds)] preparing and launching clean JOB/TPC-H/XP lanes"
cd "${ROOT}"
/home/anonymous/miniconda3/envs/resource_adaptive/bin/python \
    shell/launch_pg_algorithm_c_four_parallel2_20260809.py prepare --tag "${RUN_TAG}"
for scene_target in xp_cpu:cpu xp_io:io job_memory:memory tpch_memory:memory; do
    scene="${scene_target%%:*}"
    target="${scene_target##*:}"
    /home/anonymous/miniconda3/envs/resource_adaptive/bin/python \
        binary_plan_resource_tuner.py \
        --config "/disk1/resource_adaptive/pg_algorithm_c_four_parallel2_20260809/${RUN_TAG}/${scene}/optimizer_config.json" \
        validate-config --target "${target}" \
        --active-params seq_page_cost,random_page_cost,cpu_tuple_cost,cpu_index_tuple_cost,cpu_operator_cost,parallel_setup_cost,parallel_tuple_cost \
        --strict
done
/home/anonymous/miniconda3/envs/resource_adaptive/bin/python \
    shell/launch_pg_algorithm_c_four_parallel2_20260809.py launch --tag "${RUN_TAG}" \
    --scenes xp_cpu,xp_io,job_memory,tpch_memory

date --iso-8601=seconds > "${ARTIFACT_ROOT}/PG4_LAUNCHED"
echo "[$(date --iso-8601=seconds)] rebuild complete and four clean lanes launched"
