#!/usr/bin/env bash
set -euo pipefail

# Restore only the owned PG5 TPC-C database.  The temporary I/O boost keeps the
# restore out of the measured candidate interval; the formal 13.5+13.5 MiB/s cap is
# restored before this command returns.
container="pg5_tpcc_d2_20260722"
host="127.0.0.1"
port="59640"
user="postgres"
password="postgres"
database="fullgrid_tpcc"
template_database="template_fullgrid_tpcc_pg5"
device="259:6"
formal_read_bps="14155776"
formal_write_bps="14155776"
restore_bps="1073741824"
db_lease_lock="${TPCC_DB_LEASE_LOCK_PATH:-/tmp/pg5_tpcc_59640_db_lease.lock}"

# DROP/CREATE must never overlap a stable-point measurement.  The point runner
# holds the same lease from before warmup until its summary is durable.
exec 9>"${db_lease_lock}"
if ! flock -n 9; then
    echo "refusing TPC-C restore: database lane is already leased: ${db_lease_lock}" >&2
    exit 8
fi

if ! docker inspect "${container}" >/dev/null 2>&1; then
    echo "owned TPC-C container is missing: ${container}" >&2
    exit 2
fi
container_id="$(docker inspect -f '{{.Id}}' "${container}")"
blkio_root="/sys/fs/cgroup/blkio/docker-limited/${container_id}"
if [[ ! -d "${blkio_root}" ]]; then
    echo "owned TPC-C blkio cgroup is missing: ${blkio_root}" >&2
    exit 3
fi

restore_formal_limits() {
    printf '%s %s\n' "${device}" "${formal_read_bps}" \
        | sudo tee "${blkio_root}/blkio.throttle.read_bps_device" >/dev/null
    printf '%s %s\n' "${device}" "${formal_write_bps}" \
        | sudo tee "${blkio_root}/blkio.throttle.write_bps_device" >/dev/null
}
trap restore_formal_limits EXIT

printf '%s %s\n' "${device}" "${restore_bps}" \
    | sudo tee "${blkio_root}/blkio.throttle.read_bps_device" >/dev/null
printf '%s %s\n' "${device}" "${restore_bps}" \
    | sudo tee "${blkio_root}/blkio.throttle.write_bps_device" >/dev/null

DB_HOST="${host}" DB_PORT="${port}" DB_USER="${user}" \
DB_PASSWORD="${password}" DB_NAME="${database}" TEMPLATE_DB="${template_database}" \
    "$(dirname "$0")/safe_restore_pg_template_db.sh"

export PGPASSWORD="${password}"
psql -h "${host}" -p "${port}" -U "${user}" -d "${database}" \
    -v ON_ERROR_STOP=1 -Atc "CHECKPOINT;"

restore_formal_limits
trap - EXIT
printf 'TPC-C restore complete: database=%s template=%s read_bps=%s write_bps=%s\n' \
    "${database}" "${template_database}" "${formal_read_bps}" "${formal_write_bps}"
