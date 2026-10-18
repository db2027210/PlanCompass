#!/usr/bin/env bash
set -euo pipefail

lane=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01
source_root="$lane/source_sf10_migration_20260727"
code_attempt="$lane/experience_sgd/attempt48_global_replay_cap5_20260726"
attempt="$lane/experience_sgd/attempt57_sf10_strict_r0_seeded_cpu7p7_w36_20260727"
database=fullgrid_tpch_sf10_migrated_20260727
point="$attempt/r0/repeat_$(date +%Y%m%d_%H%M%S)_$$"
python_bin=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
container=pg5_tpch_ap_slot_node01_20260726
cpu_limit_cores=7.7
memory_limit_bytes=2820000000
io_read_cap_bytes_s=16777216
io_write_cap_bytes_s=20971520
warmup_io_read_cap_bytes_s=67108864
warmup_io_write_cap_bytes_s=83886080
io_read_cap_mib_s=16
io_write_cap_mib_s=20
io_total_cap_mib_s=36
preflight_only=0
if [[ "${1:-}" == "--preflight-only" ]]; then
    preflight_only=1
fi

mkdir -p "$attempt/r0"
unset LD_LIBRARY_PATH
export PYTHONPATH="$code_attempt/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages"
export PYTHONNOUSERSITE=1
unset PYTHONHOME

# Keep the measured denominators aligned with the real Docker/cgroup limits.
# Warmup completion previously advanced each worker's random generator by a
# variable amount. Freeze the formal measurement to a balanced seeded cycle,
# then probe between CPU7.5 and CPU8 with all other controls frozen.
# Before every point, evict only this owned database's relation pages; never
# use host-wide drop_caches.
docker update \
    --cpus "$cpu_limit_cores" \
    --memory "$memory_limit_bytes" \
    --memory-swap "$memory_limit_bytes" \
    "$container" >/dev/null

# Every point starts from the same owned-container lifecycle state.  An
# exited container has no live blkio cgroup, so start it before touching the
# cgroup; a running container is first moved to the responsive warmup caps
# and then restarted to release stale benchmark workers.
container_running="false"
if [[ "$container_running" == "true" ]]; then
    container_id="$(docker inspect -f '{{.Id}}' "$container")"
    blkio_root="/sys/fs/cgroup/blkio/docker/$container_id"
    test -d "$blkio_root"
    printf '259:2 %s\n' "$warmup_io_read_cap_bytes_s" \
        | cat "$blkio_root/blkio.throttle.read_bps_device" >/dev/null
    printf '259:2 %s\n' "$warmup_io_write_cap_bytes_s" \
        | cat "$blkio_root/blkio.throttle.write_bps_device" >/dev/null
    database_oid="$(
        docker exec "$container" psql -p 64304 -U postgres -d postgres -Atc \
            "SELECT oid FROM pg_database WHERE datname='$database'"
    )"
    [[ "$database_oid" =~ ^[0-9]+$ ]]
    docker exec "$container" psql -p 64304 -U postgres \
        -d "$database" -v ON_ERROR_STOP=1 -c CHECKPOINT >/dev/null
    docker stop --time 60 "$container" >/dev/null
    sudo env DATABASE_CACHE_ROOT="$lane/pgdata/base/$database_oid" python3 - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["DATABASE_CACHE_ROOT"])
if not root.is_dir():
    raise SystemExit(f"database cache root is absent: {root}")
files = 0
bytes_advised = 0
for path in root.rglob("*"):
    if not path.is_file():
        continue
    fd = os.open(path, os.O_RDONLY)
    try:
        size = os.fstat(fd).st_size
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        files += 1
        bytes_advised += size
    finally:
        os.close(fd)
print(
    f"scoped_cache_precondition database_root={root} "
    f"files={files} bytes={bytes_advised}",
    flush=True,
)
PY
    docker start "$container" >/dev/null 2>&1 || true
    sleep 5
else
    docker start "$container" >/dev/null 2>&1 || true
    sleep 5
fi

container_id="$(docker inspect -f '{{.Id}}' "$container")"
blkio_root="/sys/fs/cgroup/blkio/docker/$container_id"
for _ in $(seq 1 30); do
    [[ -d "$blkio_root" ]] && break
    sleep 1
done
test -d "$blkio_root"
printf '259:2 %s\n' "$warmup_io_read_cap_bytes_s" \
    | cat "$blkio_root/blkio.throttle.read_bps_device" >/dev/null
printf '259:2 %s\n' "$warmup_io_write_cap_bytes_s" \
    | cat "$blkio_root/blkio.throttle.write_bps_device" >/dev/null

postgres_ready=0
for _ in $(seq 1 120); do
    if docker exec "$container" pg_isready \
        -p 64304 -d "$database" -U postgres >/dev/null 2>&1; then
        postgres_ready=1
        break
    fi
    sleep 1
done
test "$postgres_ready" = "1"

container_id="$(docker inspect -f '{{.Id}}' "$container")"
blkio_root="/sys/fs/cgroup/blkio/docker/$container_id"
test -d "$blkio_root"

if [[ "$preflight_only" == "1" ]]; then
    printf '259:2 %s\n' "$io_read_cap_bytes_s" \
        | cat "$blkio_root/blkio.throttle.read_bps_device" >/dev/null
    printf '259:2 %s\n' "$io_write_cap_bytes_s" \
        | cat "$blkio_root/blkio.throttle.write_bps_device" >/dev/null
    test "$(cat "$blkio_root/blkio.throttle.read_bps_device")" = \
        "259:2 $io_read_cap_bytes_s"
    test "$(cat "$blkio_root/blkio.throttle.write_bps_device")" = \
        "259:2 $io_write_cap_bytes_s"
    docker inspect -f \
        'TPC-H resource preflight passed; formal IO caps verified: nano_cpus={{.HostConfig.NanoCpus}} memory={{.HostConfig.Memory}} swap={{.HostConfig.MemorySwap}}' \
        "$container"
    # Leave the database responsive for control queries while no benchmark is
    # active.  A real point reapplies the formal caps after its warmup.
    printf '259:2 %s\n' "$warmup_io_read_cap_bytes_s" \
        | cat "$blkio_root/blkio.throttle.read_bps_device" >/dev/null
    printf '259:2 %s\n' "$warmup_io_write_cap_bytes_s" \
        | cat "$blkio_root/blkio.throttle.write_bps_device" >/dev/null
    exit 0
fi

# The benchmark runner has a frozen 60-second warmup. Apply the formal caps
# five seconds before measurement, then keep them unchanged until the runner
# exits. The wrapper owns the cap controller and always reaps it, so a failed
# point cannot leave an orphan that later changes another point's cgroup.
formal_cap_controller_pid=
restore_warm_caps() {
    if [[ -n "$formal_cap_controller_pid" ]]; then
        kill "$formal_cap_controller_pid" >/dev/null 2>&1 || true
        wait "$formal_cap_controller_pid" >/dev/null 2>&1 || true
        formal_cap_controller_pid=
    fi
    if [[ -d "$blkio_root" ]]; then
        printf '259:2 %s\n' "$warmup_io_read_cap_bytes_s" \
            | cat "$blkio_root/blkio.throttle.read_bps_device" >/dev/null
        printf '259:2 %s\n' "$warmup_io_write_cap_bytes_s" \
            | cat "$blkio_root/blkio.throttle.write_bps_device" >/dev/null
    fi
}
trap restore_warm_caps EXIT INT TERM
(
    sleep 175
    printf '259:2 %s\n' "$io_read_cap_bytes_s" \
        | cat "$blkio_root/blkio.throttle.read_bps_device" >/dev/null
    printf '259:2 %s\n' "$io_write_cap_bytes_s" \
        | cat "$blkio_root/blkio.throttle.write_bps_device" >/dev/null
) &
formal_cap_controller_pid=$!

"$python_bin" -S -u "$source_root/run_pg_weighted_sql_baseline_seeded.py" \
    --port 63301 \
    --database "$database" \
    --container "$container" \
    --sql-dir "$code_attempt/code/queries" \
    --output-dir "$point" \
    --workers 36 \
    --seed 20260722 \
    --measurement-schedule balanced_seeded_cycle \
    --warmup-seconds 180 \
    --session-work-mem 1MB \
    --statement-timeout-ms 900000 \
    --minimum-seconds 180 \
    --cpu-limit-cores "$cpu_limit_cores" \
    --memory-limit-bytes "$memory_limit_bytes" \
    --io-total-limit-mib-s "$io_total_cap_mib_s" \
    --io-read-cap-mib-s "$io_read_cap_mib_s" \
    --io-write-cap-mib-s "$io_write_cap_mib_s" \
    --r0-target-resource memory \
    --r0-target-max-pct 90 \
    --stability-target-resource memory
