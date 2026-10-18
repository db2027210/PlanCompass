#!/usr/bin/env bash
set -euo pipefail

campaign_id="pg5_common_outer_bgallowed_20260722_v1"
tpcc_root="/disk2/resource_adaptive/${campaign_id}/tpcc_attempt2"
tpch_root="/disk3/resource_adaptive/${campaign_id}/tpch_attempt2"

wait_postgres() {
    local port="$1"
    for _ in $(seq 1 120); do
        if PGPASSWORD=postgres pg_isready \
            -h 127.0.0.1 -p "${port}" -U postgres -d postgres >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 2
}

finish_lane() {
    local root="$1"
    local container="$2"
    local port="$3"
    local database="$4"
    local template="$5"

    PGPASSWORD=postgres psql \
        -h 127.0.0.1 -p "${port}" -U postgres -d "${database}" \
        -v ON_ERROR_STOP=1 -c "ANALYZE;"
    PGPASSWORD=postgres createdb \
        -h 127.0.0.1 -p "${port}" -U postgres \
        -T "${database}" "${template}"
    PGPASSWORD=postgres psql \
        -h 127.0.0.1 -p "${port}" -U postgres -d postgres \
        -v ON_ERROR_STOP=1 \
        -c "ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';"
    sudo -n docker restart "${container}" >/dev/null
    wait_postgres "${port}"
    PGPASSWORD=postgres psql \
        -h 127.0.0.1 -p "${port}" -U postgres -d "${database}" \
        -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
    PGPASSWORD=postgres psql \
        -h 127.0.0.1 -p "${port}" -U postgres -d "${template}" \
        -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"

    sudo -n docker inspect "${container}" \
        > "${root}/evidence/container_inspect.json"
    PGPASSWORD=postgres psql \
        -h 127.0.0.1 -p "${port}" -U postgres -d "${database}" \
        -Atc "select current_database(), current_setting('server_version'), pg_size_pretty(pg_database_size(current_database())), current_setting('shared_preload_libraries');" \
        > "${root}/evidence/database_readback.txt"
    ps -eo user,pid,pcpu,pmem,stat,etimes,args --sort=-pcpu \
        | sed -n '1,80p' > "${root}/evidence/background_processes_at_ready.txt"
    iostat -dx 1 2 > "${root}/evidence/background_iostat_at_ready.txt"
    echo "ready_at=$(date --iso-8601=seconds)" \
        >> "${root}/evidence/lane.env"
}

resume_tpcc() {
    PGPASSWORD=postgres psql \
        -h 127.0.0.1 -p 59640 -U postgres -d fullgrid_tpcc \
        -v ON_ERROR_STOP=1 -c "CREATE SCHEMA IF NOT EXISTS public AUTHORIZATION postgres;"
    PGPASSWORD=postgres pg_restore \
        -h 127.0.0.1 -p 59640 -U postgres \
        -d fullgrid_tpcc --no-owner --no-privileges --jobs 4 \
        "${tpcc_root}/runtime/source.dump"
    finish_lane \
        "${tpcc_root}" pg5_tpcc_d2_20260722 59640 \
        fullgrid_tpcc template_fullgrid_tpcc_pg5
}

resume_tpch() {
    pg_restore -l "${tpch_root}/runtime/source.dump" >/dev/null
    sha256sum "${tpch_root}/runtime/source.dump" \
        > "${tpch_root}/evidence/source_dump.sha256"
    sudo -n docker run -d \
        --name pg5_tpch_d3_20260722 \
        --network host \
        --cpuset-cpus 136-138,392-394 \
        --cpuset-mems 1 \
        --cpus 3 \
        --memory 6g \
        --memory-swap 6g \
        --pids-limit 512 \
        --shm-size 4g \
        -e POSTGRES_PASSWORD=postgres \
        -v "${tpch_root}/pgdata:/var/lib/postgresql/data" \
        postgres:17 \
        postgres \
        -p 59650 \
        -c shared_buffers=2GB \
        -c work_mem=32MB \
        -c max_connections=256 \
        -c max_worker_processes=8 \
        -c max_parallel_workers=4 \
        -c max_parallel_workers_per_gather=2 \
        -c jit=off \
        > "${tpch_root}/evidence/container_id.txt"
    wait_postgres 59650
    PGPASSWORD=postgres createdb \
        -h 127.0.0.1 -p 59650 -U postgres fullgrid_tpch
    PGPASSWORD=postgres pg_restore \
        -h 127.0.0.1 -p 59650 -U postgres \
        -d fullgrid_tpch --no-owner --no-privileges --jobs 4 \
        "${tpch_root}/runtime/source.dump"
    finish_lane \
        "${tpch_root}" pg5_tpch_d3_20260722 59650 \
        fullgrid_tpch template_fullgrid_tpch_pg5
}

resume_tpcc > "/tmp/${campaign_id}_tpcc_resume.log" 2>&1 &
tpcc_pid=$!
resume_tpch > "/tmp/${campaign_id}_tpch_resume.log" 2>&1 &
tpch_pid=$!

echo "tpcc_resume_pid=${tpcc_pid}"
echo "tpch_resume_pid=${tpch_pid}"
wait "${tpcc_pid}"
wait "${tpch_pid}"
echo "local_lanes_ready=true"
