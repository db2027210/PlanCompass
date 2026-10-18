#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
PGDATA=${LANE_ROOT}/template/job/pgdata
BUILD=${LANE_ROOT}/template_build
DATA=${LANE_ROOT}/source/jobdata
SQL=${LANE_ROOT}/source/job_sql
TMP=${LANE_ROOT}/tmp/postgres
SOCKET=${LANE_ROOT}/socket/postgresql
LOG=${LANE_ROOT}/logs/template_build.log
CONTAINER=ra_pg5_job_template_build_v1
IMAGE=postgres:17
TABLES=(aka_name aka_title cast_info char_name comp_cast_type company_name company_type complete_cast info_type keyword kind_type link_type movie_companies movie_info movie_info_idx movie_keyword movie_link name person_info role_type title)
declare -A EXPECTED_BYTES=(
    [aka_name]=73004383 [aka_title]=38858446 [cast_info]=1418137141
    [char_name]=215711567 [comp_cast_type]=45 [company_name]=17802021
    [company_type]=92 [complete_cast]=2414495 [info_type]=1928
    [keyword]=3791536 [kind_type]=85 [link_type]=261
    [movie_companies]=93112104 [movie_info]=963782656 [movie_info_idx]=35335875
    [movie_keyword]=93799307 [movie_link]=656584 [name]=321191609
    [person_info]=399133124 [role_type]=160 [title]=203877939
)

mkdir -p "${BUILD}" "${PGDATA}" "${TMP}" "${SOCKET}"
chmod 1777 "${TMP}" "${SOCKET}"
if docker inspect "${CONTAINER}" >/dev/null 2>&1; then
    echo "BLOCKED: exact template-build container already exists: ${CONTAINER}" >&2
    exit 31
fi
if find "${PGDATA}" -mindepth 1 -print -quit | grep -q .; then
    echo "BLOCKED: template PGDATA is not empty: ${PGDATA}" >&2
    exit 32
fi
test -s "${SQL}/schema.sql"
test -s "${SQL}/fkindexes.sql"

{
    echo "TEMPLATE_BUILD_START $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    docker run --detach --name "${CONTAINER}" \
        --label resource_adaptive.campaign=pg5_common_outer_bgallowed_20260722_v1 \
        --label resource_adaptive.lane=job_node02_template_build \
        --cpus 3 --cpuset-cpus 12-17,48-53 --cpuset-mems 0 \
        --memory 6442450944 --memory-swap 6442450944 --pids-limit 512 \
        --device-read-bps /dev/nvme2n1:1205862400 \
        --device-write-bps /dev/nvme2n1:1205862400 \
        --network bridge --log-driver local --shm-size 1073741824 \
        --volume "${PGDATA}:/var/lib/postgresql/data" \
        --volume "${DATA}:/jobdata:ro" \
        --volume "${SQL}:/job_sql:ro" \
        --volume "${TMP}:/tmp" \
        --volume "${SOCKET}:/var/run/postgresql" \
        --env POSTGRES_PASSWORD=CHANGE_ME --env POSTGRES_DB=imdb_job \
        "${IMAGE}" \
        postgres -c port=5432 -c listen_addresses='*' -c fsync=on \
        -c synchronous_commit=on -c full_page_writes=on -c jit=off \
        -c track_io_timing=on -c shared_buffers=4864MB -c effective_cache_size=5GB \
        -c work_mem=4MB -c maintenance_work_mem=64MB \
        -c max_worker_processes=16 -c max_parallel_workers=8 \
        -c max_parallel_workers_per_gather=2 -c max_connections=64
    for _ in $(seq 1 120); do
        if docker exec "${CONTAINER}" pg_isready -U postgres -d imdb_job >/dev/null 2>&1; then
            break
        fi
        sleep 2
    done
    docker exec "${CONTAINER}" pg_isready -U postgres -d imdb_job
    echo "SCHEMA_LOAD_START $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    docker exec "${CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d imdb_job -f /job_sql/schema.sql
    : >"${BUILD}/row_counts.tsv"
    for table in "${TABLES[@]}"; do
        for _ in $(seq 1 1440); do
            actual_bytes=$(stat -c '%s' "${DATA}/${table}.csv" 2>/dev/null || echo 0)
            if test "${actual_bytes}" -eq "${EXPECTED_BYTES[$table]}"; then
                break
            fi
            if test "${actual_bytes}" -gt "${EXPECTED_BYTES[$table]}"; then
                echo "BLOCKED: ${table}.csv exceeds expected size: ${actual_bytes} > ${EXPECTED_BYTES[$table]}" >&2
                exit 34
            fi
            sleep 5
        done
        if test "${actual_bytes}" -ne "${EXPECTED_BYTES[$table]}"; then
            echo "BLOCKED: timed out waiting for complete ${table}.csv: ${actual_bytes}/${EXPECTED_BYTES[$table]}" >&2
            exit 35
        fi
        echo "COPY_START table=${table} at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        copy_result=$(docker exec "${CONTAINER}" psql -A -t -v ON_ERROR_STOP=1 -U postgres -d imdb_job \
            -c "COPY ${table} FROM '/jobdata/${table}.csv' WITH (FORMAT csv, ESCAPE E'\\\\');")
        count=${copy_result#COPY }
        if ! [[ "${count}" =~ ^[0-9]+$ ]]; then
            echo "BLOCKED: invalid COPY row count for ${table}: ${copy_result}" >&2
            exit 33
        fi
        printf '%s\t%s\n' "${table}" "${count}" >>"${BUILD}/row_counts.tsv"
        echo "COPY_COMPLETE table=${table} rows=${count} at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    done
    echo "INDEX_BUILD_START $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    docker exec "${CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d imdb_job -f /job_sql/fkindexes.sql
    docker exec "${CONTAINER}" psql -v ON_ERROR_STOP=1 -U postgres -d imdb_job -c 'ANALYZE; CHECKPOINT;'
    docker exec "${CONTAINER}" psql -A -t -U postgres -d imdb_job -c 'SHOW server_version;' >"${BUILD}/postgresql_version.txt"
    docker exec "${CONTAINER}" psql -A -t -U postgres -d imdb_job -c 'SHOW default_statistics_target;' >"${BUILD}/default_statistics_target.txt"
    docker exec "${CONTAINER}" psql -A -t -U postgres -d imdb_job -c 'SELECT pg_database_size(current_database());' >"${BUILD}/database_size_bytes.txt"
    docker inspect "${CONTAINER}" >"${BUILD}/container_inspect_before_stop.json"
    docker stop --time 120 "${CONTAINER}"
    docker logs "${CONTAINER}" >"${BUILD}/container_stdout.log" 2>"${BUILD}/container_stderr.log" || true
    docker rm "${CONTAINER}"
    docker run --rm --network none --volume "${PGDATA}:/pgdata" \
        --entrypoint sh "${IMAGE}" -c 'chown -R 1042:1043 /pgdata && chmod -R a+rX,a-w /pgdata'
    find "${PGDATA}" -type f -printf '%s\n' >"${BUILD}/regular_file_sizes.txt"
    "${LANE_ROOT}/runtime/python/bin/python" "${LANE_ROOT}/launch/finalize_template.py"
    chmod a-w "${LANE_ROOT}/template/job/template_manifest.json"
    echo "TEMPLATE_BUILD_COMPLETE $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >>"${LOG}" 2>&1
