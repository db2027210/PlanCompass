#!/usr/bin/env bash
set -euo pipefail

lane_root=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpch_node04
container=pg5_tpch_s04_20260722
database=template_fullgrid_tpch_pg5
port=64304
source_launcher="$lane_root/launch_pg5_tpch_node04_r0_attempt29_20260722.sh"
source_launcher_sha256=a1af1c8d776bb7323d2c4a459c8789b209cd307e06b760098fad8e14a7535e2f
runner="$lane_root/code/run_pg_weighted_sql_baseline.py"
runner_sha256=2b1ffcd8326a024e47644debaed667cb3ab469c10863a3dd85a451d94304e452
protocol="$lane_root/tpch_node04_attempt33_reproducibility_protocol.json"
protocol_sha256=30389284e5fa2f08969678c280eb61dfc508896a9f9ae508aee717e4123425ef
evidence_dir="$lane_root/evidence/r0_attempt_33_reproducibility"
backend_pattern="postgres: postgres $database 127.0.0.1"

test "$(sha256sum "$source_launcher" | awk '{print $1}')" = "$source_launcher_sha256"
test "$(sha256sum "$runner" | awk '{print $1}')" = "$runner_sha256"
test "$(sha256sum "$protocol" | awk '{print $1}')" = "$protocol_sha256"
test ! -e "$lane_root/baseline_r0_attempt_33"
test ! -e "$lane_root/runtime/baseline_r0_attempt_33.pid"
if pgrep -f '[r]un_pg_weighted_sql_baseline.py.*--port 64304' >/dev/null; then
    echo "owned TPC-H runner is still active" >&2
    exit 4
fi

mkdir -p "$evidence_dir"
container_id="$(docker inspect -f '{{.Id}}' "$container")"
blkio_root="/sys/fs/cgroup/blkio/docker/$container_id"
cat "$blkio_root/blkio.throttle.read_bps_device" > "$evidence_dir/blkio_before_cleanup_read.txt"
cat "$blkio_root/blkio.throttle.write_bps_device" > "$evidence_dir/blkio_before_cleanup_write.txt"
test "$(cat "$evidence_dir/blkio_before_cleanup_read.txt")" = "259:3 13312"
test "$(cat "$evidence_dir/blkio_before_cleanup_write.txt")" = "259:3 13312"

docker exec "$container" sh -c \
    "pids=\$(ps -eo pid=,args= | grep -F '$backend_pattern' | grep -v grep | awk '{print \$1}'); \
     if [ -n \"\$pids\" ]; then kill -TERM \$pids; fi"

backend_count=0
for _ in $(seq 1 300); do
    backend_count="$(docker exec "$container" sh -c \
        "ps -eo args= | grep -F '$backend_pattern' | grep -v grep | wc -l")"
    if [ "$backend_count" -eq 0 ]; then
        break
    fi
    sleep 2
done
test "$backend_count" -eq 0

cat "$blkio_root/blkio.throttle.read_bps_device" > "$evidence_dir/blkio_after_cleanup_read.txt"
cat "$blkio_root/blkio.throttle.write_bps_device" > "$evidence_dir/blkio_after_cleanup_write.txt"
test "$(cat "$evidence_dir/blkio_after_cleanup_read.txt")" = "259:3 13312"
test "$(cat "$evidence_dir/blkio_after_cleanup_write.txt")" = "259:3 13312"
printf 'remaining_owned_backend_count=%s\n' "$backend_count" \
    > "$evidence_dir/pre_restart_backend_readback.txt"

docker inspect "$container" > "$evidence_dir/container_before_restart.json"
docker restart --time 60 "$container" > "$evidence_dir/container_restart.txt"

postgres_ready=false
for _ in $(seq 1 120); do
    if docker exec "$container" pg_isready -p "$port" -d "$database" -U postgres >/dev/null 2>&1; then
        postgres_ready=true
        break
    fi
    sleep 1
done
test "$postgres_ready" = true

post_restart_backend_count="$(docker exec "$container" sh -c \
    "ps -eo args= | grep -F '$backend_pattern' | grep -v grep | wc -l")"
test "$post_restart_backend_count" -eq 0
docker inspect "$container" > "$evidence_dir/container_after_restart.json"
printf 'postgres_ready=%s\npost_restart_owned_backend_count=%s\nfixed_warmup_seconds=60\n' \
    "$postgres_ready" "$post_restart_backend_count" \
    > "$evidence_dir/post_restart_readback.txt"

ATTEMPT_ID=33 \
CPU_LIMIT_CORES=10.8 \
MEMORY_LIMIT_BYTES=2147483648 \
MEMORY_LIMIT_DOCKER=2048m \
IO_READ_CAP_BYTES_S=13312 \
IO_WRITE_CAP_BYTES_S=13312 \
IO_READ_CAP_MIB_S=0.0126953125 \
IO_WRITE_CAP_MIB_S=0.0126953125 \
IO_TOTAL_CAP_MIB_S=0.025390625 \
WORKERS=32 \
exec "$source_launcher"
