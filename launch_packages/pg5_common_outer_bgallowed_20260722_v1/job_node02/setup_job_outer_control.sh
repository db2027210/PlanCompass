#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
CONTROL_ROOT=${RA_PG_CONTROL_ROOT:-${LANE_ROOT}/work/control_outer_attempt1}
OUTPUT_ROOT=${RA_PG_OUTPUT_ROOT:-${LANE_ROOT}/history/job/outer/attempt_1}
PGDATA=${CONTROL_ROOT}/pgdata
CONTAINER=ra_pg5_job_node02_v1_outercontrol
PORT=64309
TMP_DIR=${LANE_ROOT}/tmp/postgres_outer_control
SOCKET_DIR=${LANE_ROOT}/socket/postgresql_outer_control

if docker inspect "${CONTAINER}" >/dev/null 2>&1; then
    echo "control container already exists: ${CONTAINER}" >&2
    exit 2
fi
if [[ -e "${CONTROL_ROOT}" ]]; then
    echo "control root already exists: ${CONTROL_ROOT}" >&2
    exit 2
fi

mkdir -p "${CONTROL_ROOT}" "${TMP_DIR}" "${SOCKET_DIR}"
docker run --rm --network none \
    --volume "${TMP_DIR}:/tmp-target" \
    --volume "${SOCKET_DIR}:/socket-target" \
    --entrypoint sh postgres:17 \
    -c 'chmod 0777 /tmp-target /socket-target'
cp -a "${LANE_ROOT}/template/job/pgdata" "${PGDATA}"
chmod -R u+w "${PGDATA}"

docker run --detach \
    --name "${CONTAINER}" \
    --label resource_adaptive.campaign=pg5_common_outer_bgallowed_20260722_v1 \
    --label resource_adaptive.lane=job_node02 \
    --label resource_adaptive.role=outer_plan_control \
    --publish "127.0.0.1:${PORT}:5432" \
    --volume "${PGDATA}:/var/lib/postgresql/data" \
    --volume "${TMP_DIR}:/tmp" \
    --volume "${SOCKET_DIR}:/var/run/postgresql" \
    --log-driver local \
    --shm-size 1073741824 \
    --health-cmd 'pg_isready -U postgres -d imdb_job' \
    --health-interval 5s --health-timeout 3s --health-retries 36 \
    --cpus 3.0 \
    --cpuset-cpus 12-17,48-53 \
    --cpuset-mems 0 \
    --memory 3124756480 \
    --memory-swap 3124756480 \
    --pids-limit 512 \
    --network bridge \
    --device-read-bps /dev/nvme2n1:134217728 \
    --device-write-bps /dev/nvme2n1:134217728 \
    postgres:17 postgres \
    -c port=5432 \
    -c 'listen_addresses=*' \
    -c 'default_transaction_isolation=read committed' \
    -c effective_cache_size=3GB \
    -c fsync=on \
    -c full_page_writes=on \
    -c jit=off \
    -c maintenance_work_mem=64MB \
    -c max_connections=64 \
    -c max_parallel_workers=8 \
    -c max_parallel_workers_per_gather=2 \
    -c max_worker_processes=16 \
    -c parallel_leader_participation=on \
    -c shared_buffers=2GB \
    -c shared_preload_libraries=pg_stat_statements \
    -c synchronous_commit=on \
    -c track_io_timing=on \
    -c work_mem=4MB >/dev/null

deadline=$((SECONDS + 180))
while (( SECONDS < deadline )); do
    state=$(docker inspect --format '{{.State.Health.Status}}' "${CONTAINER}" 2>/dev/null || true)
    if [[ "${state}" == healthy ]]; then
        break
    fi
    if [[ "$(docker inspect --format '{{.State.Running}}' "${CONTAINER}" 2>/dev/null || true)" != true ]]; then
        docker logs "${CONTAINER}" >&2 || true
        exit 3
    fi
    sleep 2
done
if [[ "$(docker inspect --format '{{.State.Health.Status}}' "${CONTAINER}")" != healthy ]]; then
    echo "control container did not become healthy" >&2
    exit 3
fi

docker exec -i -u postgres "${CONTAINER}" psql -v ON_ERROR_STOP=1 -d postgres <<'SQL'
ALTER ROLE postgres PASSWORD 'CHANGE_ME';
SQL
docker exec -i -u postgres "${CONTAINER}" psql -v ON_ERROR_STOP=1 -d imdb_job <<'SQL'
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SQL

mkdir -p "${OUTPUT_ROOT}"
docker inspect "${CONTAINER}" >"${OUTPUT_ROOT}/control_container_inspect.json"
echo "outer control ready: ${CONTAINER} port=${PORT}"
