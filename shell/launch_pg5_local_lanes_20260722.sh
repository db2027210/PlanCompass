#!/usr/bin/env bash
set -euo pipefail

campaign_id="pg5_common_outer_bgallowed_20260722_v1"
source_port="59522"
source_container="ra_fullgrid_generic_20260718_w2"
source_image="postgres:17"
source_root="/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated"

provision_lane() {
    local lane="$1"
    local target_root="$2"
    local source_database="$3"
    local target_database="$4"
    local template_database="$5"
    local container_name="$6"
    local port="$7"
    local cpuset="$8"
    local cpuset_mems="$9"
    local cpu_limit="${10}"
    local memory_limit="${11}"
    local shm_size="${12}"
    local shared_buffers="${13}"
    local work_mem="${14}"

    if [[ -e "${target_root}" ]]; then
        echo "target already exists: ${target_root}" >&2
        return 2
    fi
    if sudo -n docker container inspect "${container_name}" >/dev/null 2>&1; then
        echo "container already exists: ${container_name}" >&2
        return 2
    fi

    mkdir -p "${target_root}/evidence" "${target_root}/runtime"
    sudo -n install -d -o 999 -g 999 -m 0700 "${target_root}/pgdata"

    {
        echo "campaign_id=${campaign_id}"
        echo "lane=${lane}"
        echo "background_load_allowed=true"
        echo "started_at=$(date --iso-8601=seconds)"
        echo "source_container=${source_container}"
        echo "source_database=${source_database}"
        echo "target_database=${target_database}"
        echo "target_root=${target_root}"
        echo "port=${port}"
        echo "cpuset=${cpuset}"
        echo "cpuset_mems=${cpuset_mems}"
    } > "${target_root}/evidence/lane.env"

    PGPASSWORD=postgres pg_dump \
        -h 127.0.0.1 -p "${source_port}" -U postgres \
        -d "${source_database}" -Fc \
        -f "${target_root}/runtime/source.dump"
    sha256sum "${target_root}/runtime/source.dump" \
        > "${target_root}/evidence/source_dump.sha256"

    sudo -n docker run -d \
        --name "${container_name}" \
        --network host \
        --cpuset-cpus "${cpuset}" \
        --cpuset-mems "${cpuset_mems}" \
        --cpus "${cpu_limit}" \
        --memory "${memory_limit}" \
        --memory-swap "${memory_limit}" \
        --pids-limit 512 \
        --shm-size "${shm_size}" \
        -e POSTGRES_PASSWORD=postgres \
        -v "${target_root}/pgdata:/var/lib/postgresql/data" \
        "${source_image}" \
        postgres \
        -p "${port}" \
        -c "shared_buffers=${shared_buffers}" \
        -c "work_mem=${work_mem}" \
        -c max_connections=256 \
        -c max_worker_processes=8 \
        -c max_parallel_workers=4 \
        -c max_parallel_workers_per_gather=2 \
        -c jit=off \
        > "${target_root}/evidence/container_id.txt"

    local ready=0
    for _ in $(seq 1 120); do
        if PGPASSWORD=postgres pg_isready \
            -h 127.0.0.1 -p "${port}" -U postgres -d postgres >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 1
    done
    if [[ "${ready}" != "1" ]]; then
        echo "PostgreSQL did not become ready: ${container_name}" >&2
        return 2
    fi

    PGPASSWORD=postgres createdb \
        -h 127.0.0.1 -p "${port}" -U postgres "${target_database}"
    PGPASSWORD=postgres psql \
        -h 127.0.0.1 -p "${port}" -U postgres -d "${target_database}" \
        -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE;"
    PGPASSWORD=postgres pg_restore \
        -h 127.0.0.1 -p "${port}" -U postgres \
        -d "${target_database}" --no-owner --no-privileges --jobs 4 \
        "${target_root}/runtime/source.dump"
    PGPASSWORD=postgres psql \
        -h 127.0.0.1 -p "${port}" -U postgres -d "${target_database}" \
        -v ON_ERROR_STOP=1 -c "ANALYZE;"
    PGPASSWORD=postgres createdb \
        -h 127.0.0.1 -p "${port}" -U postgres \
        -T "${target_database}" "${template_database}"

    sudo -n docker inspect "${container_name}" \
        > "${target_root}/evidence/container_inspect.json"
    PGPASSWORD=postgres psql \
        -h 127.0.0.1 -p "${port}" -U postgres -d "${target_database}" \
        -Atc "select current_database(), current_setting('server_version'), pg_size_pretty(pg_database_size(current_database()));" \
        > "${target_root}/evidence/database_readback.txt"
    ps -eo user,pid,pcpu,pmem,stat,etimes,args --sort=-pcpu \
        | sed -n '1,80p' > "${target_root}/evidence/background_processes_at_ready.txt"
    iostat -dx 1 2 > "${target_root}/evidence/background_iostat_at_ready.txt"
    echo "ready_at=$(date --iso-8601=seconds)" \
        >> "${target_root}/evidence/lane.env"
}

tpcc_root="/disk2/resource_adaptive/${campaign_id}/tpcc_attempt2"
tpch_root="/disk3/resource_adaptive/${campaign_id}/tpch_attempt2"

provision_lane \
    Local-D2 "${tpcc_root}" \
    fullgrid_tpcc fullgrid_tpcc template_fullgrid_tpcc_pg5 \
    pg5_tpcc_d2_20260722 59640 104-107,360-363 0 4 16g 8g 4GB 32MB \
    > "/tmp/${campaign_id}_tpcc_provision.log" 2>&1 &
tpcc_pid=$!

provision_lane \
    Local-D3 "${tpch_root}" \
    fullgrid_tpch fullgrid_tpch template_fullgrid_tpch_pg5 \
    pg5_tpch_d3_20260722 59650 136-138,392-394 1 3 6g 4g 2GB 32MB \
    > "/tmp/${campaign_id}_tpch_provision.log" 2>&1 &
tpch_pid=$!

echo "tpcc_provision_pid=${tpcc_pid}"
echo "tpch_provision_pid=${tpch_pid}"
echo "tpcc_log=/tmp/${campaign_id}_tpcc_provision.log"
echo "tpch_log=/tmp/${campaign_id}_tpch_provision.log"

wait "${tpcc_pid}"
wait "${tpch_pid}"

echo "local_lanes_ready=true"
