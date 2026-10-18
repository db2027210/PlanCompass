#!/usr/bin/env bash
set -euo pipefail

worker_root=/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/workers
scene=${1:?usage: provision_pg_algorithm_a_replay_farm_worker.sh SCENE WORKER_INDEX}
worker_index=${2:?usage: provision_pg_algorithm_a_replay_farm_worker.sh SCENE WORKER_INDEX}

if [[ ! ${worker_index} =~ ^[3-8]$ ]]; then
    echo "worker index must be 3..8" >&2
    exit 2
fi

case "${scene}:${worker_index}" in
    job:3)
        source_port=59523 target_port=59525
        container=ra_fullgrid_generic_mem_20260718_w3
        data_dir=${worker_root}/generic_memory/w3/pgdata
        cpuset=32-39,88-95,184-187 cpus=20 memory=8g shm_size=2g
        expected_db=fullgrid_job expected_template=template_fullgrid_job
        ;;
    job:4)
        source_port=59524 target_port=59526
        container=ra_fullgrid_generic_mem_20260718_w4
        data_dir=${worker_root}/generic_memory/w4/pgdata
        cpuset=40-47,96-103,188-191 cpus=20 memory=8g shm_size=2g
        expected_db=fullgrid_job expected_template=template_fullgrid_job
        ;;
    tpch:3)
        source_port=59521 target_port=59527
        container=ra_fullgrid_generic_20260718_w3
        data_dir=${worker_root}/generic/w3/pgdata
        # Combine the two completed XP-CPU slots into one equal 8-core worker.
        # HyBench PGDATA is never reused: every worker is a fresh TPC-H clone.
        cpuset=80-87 cpus=8 memory=8g shm_size=8g
        expected_db=fullgrid_tpch_sf5 expected_template=template_fullgrid_tpch_sf5
        ;;
    tpch:4)
        source_port=59522 target_port=59528
        container=ra_fullgrid_generic_20260718_w4
        data_dir=${worker_root}/generic/w4/pgdata
        cpuset=32-39 cpus=8 memory=8g shm_size=8g
        expected_db=fullgrid_tpch_sf5 expected_template=template_fullgrid_tpch_sf5
        ;;
    tpch:5)
        # Clone from the already-provisioned, quiescent TPC-H w3 worker.
        # The original w1/w2 workers may still contain DROP/CREATE DATABASE WAL
        # from the round-boundary clean restore.
        source_port=59527 target_port=59529
        container=ra_fullgrid_generic_20260718_w5
        data_dir=${worker_root}/generic/w5/pgdata
        cpuset=40-47 cpus=8 memory=8g shm_size=8g
        expected_db=fullgrid_tpch_sf5 expected_template=template_fullgrid_tpch_sf5
        ;;
    tpch:6)
        # Clone from the already-provisioned, quiescent TPC-H w4 worker.
        source_port=59528 target_port=59530
        container=ra_fullgrid_generic_20260718_w6
        data_dir=${worker_root}/generic/w6/pgdata
        cpuset=88-95 cpus=8 memory=8g shm_size=8g
        expected_db=fullgrid_tpch_sf5 expected_template=template_fullgrid_tpch_sf5
        ;;
    tpch:7)
        source_port=59527 target_port=59531
        container=ra_fullgrid_generic_20260718_w7
        data_dir=${worker_root}/generic/w7/pgdata
        # Reuse JOB w1's released 8-core subset only after JOB accepted=7.
        cpuset=48-55 cpus=8 memory=8g shm_size=8g
        expected_db=fullgrid_tpch_sf5 expected_template=template_fullgrid_tpch_sf5
        ;;
    tpch:8)
        source_port=59528 target_port=59532
        container=ra_fullgrid_generic_20260718_w8
        data_dir=${worker_root}/generic/w8/pgdata
        # Reuse JOB w2's released 8-core subset only after JOB accepted=7.
        cpuset=56-63 cpus=8 memory=8g shm_size=8g
        expected_db=fullgrid_tpch_sf5 expected_template=template_fullgrid_tpch_sf5
        ;;
    *)
        echo "unsupported worker: ${scene}:${worker_index}" >&2
        exit 2
        ;;
esac

if [[ ${container} == *mysql* || ${data_dir} == *mysql* ]]; then
    echo "refusing to touch a MySQL identity" >&2
    exit 2
fi

size_to_bytes() {
    local size=${1:?size is required}
    case "${size}" in
        *g) echo $(( ${size%g} * 1024 * 1024 * 1024 )) ;;
        *m) echo $(( ${size%m} * 1024 * 1024 )) ;;
        *) echo "${size}" ;;
    esac
}

validate_existing_worker() {
    local inspect_row
    local expected_nano_cpus=$(( cpus * 1000000000 ))
    local expected_memory_bytes
    local expected_shm_bytes
    expected_memory_bytes=$(size_to_bytes "${memory}")
    expected_shm_bytes=$(size_to_bytes "${shm_size}")
    inspect_row=$(docker inspect --format \
        '{{.State.Status}}|{{.HostConfig.NetworkMode}}|{{.HostConfig.CpusetCpus}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.Memory}}|{{.HostConfig.ShmSize}}|{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Source}}{{end}}{{end}}' \
        "${container}" 2>/dev/null) || return 1
    [[ ${inspect_row} == "running|host|${cpuset}|${expected_nano_cpus}|${expected_memory_bytes}|${expected_shm_bytes}|${data_dir}" ]] \
        || return 1
    PGPASSWORD=postgres psql \
        -h 127.0.0.1 -p "${target_port}" -U postgres -d postgres \
        -X -q -v ON_ERROR_STOP=1 -At \
        -c "SELECT datname FROM pg_database WHERE datname IN ('${expected_db}', '${expected_template}') ORDER BY 1" \
        2>/dev/null \
        | diff -u - <(printf '%s\n%s\n' "${expected_db}" "${expected_template}" | sort) \
        >/dev/null
}

if docker inspect "${container}" >/dev/null 2>&1 \
    || ss -ltnH "sport = :${target_port}" | grep -q . \
    || [[ -e ${data_dir} ]]; then
    if validate_existing_worker; then
        echo "scene=${scene}"
        echo "worker=w${worker_index}"
        echo "container=${container}"
        echo "port=${target_port}"
        echo "source_port=${source_port}"
        echo "status=ready_existing"
        exit 0
    fi
    echo "target worker artifacts exist but do not match the required identity: ${container}" >&2
    exit 3
fi

source_databases=$(PGPASSWORD=postgres psql \
    -h 127.0.0.1 -p "${source_port}" -U postgres -d postgres \
    -X -q -v ON_ERROR_STOP=1 -At \
    -c "SELECT datname FROM pg_database WHERE datname IN ('${expected_db}', '${expected_template}') ORDER BY 1")
if ! diff -u \
    <(printf '%s\n' "${source_databases}") \
    <(printf '%s\n%s\n' "${expected_db}" "${expected_template}" | sort) \
    >/dev/null; then
    echo "source workload/template database gate failed on port ${source_port}" >&2
    exit 5
fi
PGPASSWORD=postgres psql \
    -h 127.0.0.1 -p "${source_port}" -U postgres -d postgres \
    -X -q -v ON_ERROR_STOP=1 -At \
    -c "SELECT current_setting('server_version_num')::int >= 170000" \
    | grep -qx t

echo "Creating ${scene} worker ${worker_index} from port ${source_port}" >&2
sudo mkdir -p "${data_dir}"
sudo chown "$(id -u):$(id -g)" "${data_dir}"
PGPASSWORD=postgres pg_basebackup \
    -h 127.0.0.1 -p "${source_port}" -U postgres \
    -D "${data_dir}" -Fp -Xs -P --checkpoint=fast
sudo chown -R 999:999 "${data_dir}"

docker run -d \
    --name "${container}" \
    --network host \
    --cpus "${cpus}" \
    --cpuset-cpus "${cpuset}" \
    --memory "${memory}" \
    --shm-size "${shm_size}" \
    -e POSTGRES_PASSWORD=postgres \
    -e POSTGRES_HOST_AUTH_METHOD=trust \
    -v "${data_dir}:/var/lib/postgresql/data" \
    postgres:17 \
    postgres \
    -c "port=${target_port}" \
    -c 'listen_addresses=*' \
    -c 'shared_preload_libraries=pg_stat_statements' \
    -c 'track_io_timing=on'

for _attempt in $(seq 1 60); do
    if PGPASSWORD=postgres psql \
        -h 127.0.0.1 -p "${target_port}" -U postgres -d postgres \
        -X -q -At -c 'SELECT 1' 2>/dev/null | grep -qx 1; then
        break
    fi
    sleep 2
done

PGPASSWORD=postgres psql \
    -h 127.0.0.1 -p "${target_port}" -U postgres -d postgres \
    -X -q -v ON_ERROR_STOP=1 -At \
    -c "SELECT datname FROM pg_database WHERE datname IN ('${expected_db}', '${expected_template}') ORDER BY 1" \
    >"/tmp/${container}_database_check.txt"
if [[ $(wc -l <"/tmp/${container}_database_check.txt") -ne 2 ]]; then
    echo "cloned worker is missing the workload/template databases" >&2
    exit 4
fi

cat <<EOF
scene=${scene}
worker=w${worker_index}
container=${container}
port=${target_port}
cpuset=${cpuset}
cpus=${cpus}
memory=${memory}
data_dir=${data_dir}
source_port=${source_port}
status=ready
EOF
