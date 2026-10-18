#!/usr/bin/env bash
set -euo pipefail

campaign_id="pg5_common_outer_bgallowed_20260722_v1"
lane_root="/mnt/nvme2n1/anonymous/resource_adaptive/${campaign_id}/tpch_node04"
container="pg5_tpch_s04_20260722"
port="64304"
database="fullgrid_tpch"
template_database="template_fullgrid_tpch_pg5"
dump_path="${lane_root}/source/tpch_sf10_pg17.dump"
python_archive="${lane_root}/source/python311_c7.tar.gz"
python_root="${lane_root}/runtime/python311"
expected_dump_sha256="5300a00ce288b67293bbbef7cf9879a4cd932b01e9c956f112614a6e115eea32"

wait_postgres() {
    for _ in $(seq 1 180); do
        if docker exec "${container}" pg_isready -p "${port}" -U postgres -d postgres >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 2
}

mkdir -p \
    "${lane_root}/runtime" \
    "${lane_root}/evidence" \
    "${lane_root}/logs" \
    "${lane_root}/background" \
    "${lane_root}/pgdata"
chmod 700 "${lane_root}" "${lane_root}/pgdata"

if docker inspect "${container}" >/dev/null 2>&1; then
    echo "container already exists: ${container}" >&2
    exit 3
fi
if command -v ss >/dev/null 2>&1 \
    && ss -lnt | awk '{print $4}' | grep -Eq "(^|:)${port}$"; then
    echo "port already in use: ${port}" >&2
    exit 4
fi

actual_dump_sha256="$(sha256sum "${dump_path}" | awk '{print $1}')"
if [[ "${actual_dump_sha256}" != "${expected_dump_sha256}" ]]; then
    echo "TPC-H dump checksum mismatch: ${actual_dump_sha256}" >&2
    exit 5
fi
printf '%s  %s\n' "${actual_dump_sha256}" "${dump_path}" \
    > "${lane_root}/evidence/source_dump.sha256"

if [[ ! -x "${python_root}/bin/python" ]]; then
    mkdir -p "${python_root}"
    tar -xzf "${python_archive}" -C "${python_root}"
fi
"${python_root}/bin/python" -c 'import psycopg2; print("python/psycopg2 ready")' \
    > "${lane_root}/evidence/python_readback.txt"

docker run -d \
    --name "${container}" \
    --network host \
    --cpuset-cpus 0-2,36-38 \
    --cpuset-mems 0 \
    --cpus 3 \
    --memory 6g \
    --memory-swap 6g \
    --pids-limit 512 \
    --shm-size 4g \
    --device-read-bps /dev/nvme2n1:1150mb \
    --device-write-bps /dev/nvme2n1:1150mb \
    -e POSTGRES_PASSWORD=postgres \
    -v "${lane_root}/pgdata:/var/lib/postgresql/data:Z" \
    -v "${lane_root}/source:/source:ro,Z" \
    postgres:17 \
    postgres \
    -p "${port}" \
    -c shared_buffers=2GB \
    -c work_mem=32MB \
    -c max_connections=256 \
    -c max_worker_processes=8 \
    -c max_parallel_workers=4 \
    -c max_parallel_workers_per_gather=2 \
    -c jit=off \
    > "${lane_root}/evidence/container_id.txt"

wait_postgres
docker exec "${container}" createdb -p "${port}" -U postgres "${database}"
docker exec "${container}" pg_restore \
    -p "${port}" \
    -U postgres \
    -d "${database}" \
    --no-owner \
    --no-privileges \
    --jobs 4 \
    /source/tpch_sf10_pg17.dump
docker exec "${container}" psql \
    -p "${port}" -U postgres -d "${database}" -v ON_ERROR_STOP=1 -c 'ANALYZE;'
docker exec "${container}" createdb \
    -p "${port}" -U postgres -T "${database}" "${template_database}"
docker exec "${container}" psql \
    -p "${port}" -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';"
docker restart "${container}" >/dev/null
wait_postgres
docker exec "${container}" psql \
    -p "${port}" -U postgres -d "${database}" -v ON_ERROR_STOP=1 \
    -c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements;'
docker exec "${container}" psql \
    -p "${port}" -U postgres -d "${template_database}" -v ON_ERROR_STOP=1 \
    -c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements;'

docker inspect "${container}" > "${lane_root}/evidence/container_inspect.json"
docker exec "${container}" psql \
    -p "${port}" -U postgres -d "${database}" -Atc \
    "select current_database(), current_setting('server_version'), pg_size_pretty(pg_database_size(current_database())), current_setting('shared_preload_libraries');" \
    > "${lane_root}/evidence/database_readback.txt"
docker exec "${container}" psql \
    -p "${port}" -U postgres -d "${database}" -Atc \
    'select (select count(*) from lineitem),(select count(*) from orders),(select count(*) from customer);' \
    > "${lane_root}/evidence/row_counts.txt"
ps -eo user,pid,pcpu,pmem,stat,etimes,args --sort=-pcpu \
    | sed -n '1,100p' > "${lane_root}/evidence/background_processes_at_ready.txt"
iostat -dx 1 3 > "${lane_root}/evidence/background_iostat_at_ready.txt"
lscpu -e=CPU,NODE,SOCKET,CORE,ONLINE \
    > "${lane_root}/evidence/cpu_numa_readback.txt"
findmnt -rn -o TARGET,SOURCE,FSTYPE,OPTIONS /mnt/nvme2n1 \
    > "${lane_root}/evidence/storage_readback.txt"

nohup mpstat 15 > "${lane_root}/background/mpstat_15s.log" 2>&1 < /dev/null &
echo "$!" > "${lane_root}/background/mpstat.pid"
nohup iostat -dx 15 > "${lane_root}/background/iostat_15s.log" 2>&1 < /dev/null &
echo "$!" > "${lane_root}/background/iostat.pid"
nohup vmstat 15 > "${lane_root}/background/vmstat_15s.log" 2>&1 < /dev/null &
echo "$!" > "${lane_root}/background/vmstat.pid"

nohup env PYTHONPATH="${lane_root}/code" \
    "${python_root}/bin/python" \
    "${lane_root}/code/run_pg_weighted_sql_baseline.py" \
    --port "${port}" \
    --database "${database}" \
    --container "${container}" \
    --sql-dir "${lane_root}/code/queries" \
    --output-dir "${lane_root}/baseline_r0" \
    --workers 2 \
    --cpu-limit-cores 3 \
    --memory-limit-bytes 6442450944 \
    --io-limit-mib-s 1150 \
    > "${lane_root}/logs/baseline_r0.log" 2>&1 < /dev/null &
baseline_pid="$!"
echo "${baseline_pid}" > "${lane_root}/runtime/baseline_r0.pid"

{
    echo "campaign_id=${campaign_id}"
    echo "benchmark=tpch"
    echo "host=node04"
    echo "storage=/mnt/nvme2n1"
    echo "container=${container}"
    echo "port=${port}"
    echo "cpuset=0-2,36-38"
    echo "cpuset_mems=0"
    echo "baseline_pid=${baseline_pid}"
    echo "started_at=$(date --iso-8601=seconds)"
} > "${lane_root}/evidence/lane.env"

echo "TPC-H node04 R0 started: pid=${baseline_pid} root=${lane_root}"
