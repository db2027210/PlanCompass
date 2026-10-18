#!/usr/bin/env bash
set -euo pipefail

lane_root=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpch_node04
container=pg5_tpch_s04_20260722
database=template_fullgrid_tpch_pg5
port=64304
source_launcher="$lane_root/launch_pg5_tpch_node04_r0_v4b_base_20260723.sh"
source_launcher_sha256=b3ac1c6b1dca1b2ed54aaf573cbc154255017bd8b0aff2f7e00e203c445b8761
runner="$lane_root/code/run_pg_weighted_sql_baseline.py"
runner_sha256=4833b1f122ecc81849ea1d1a16d0b07cccb0a652de6aedad23b21cc2e23c4ee7
formal_contracts="$lane_root/code/formal_contracts.py"
formal_contracts_sha256=85822a22364e1eb98498f680bc397c953b0e3337befd9cbad0d37b5bb49d5c91
stability_contract="$lane_root/code/stability_gate_contract_v4.json"
stability_contract_sha256=ba2810b36fd1caf36b8f6679e380e5e97f13ff5d357d3227fbfd7d0219393853
protocol="$lane_root/tpch_node04_attempt40_reproducibility_protocol.json"
protocol_sha256=133e485b34cfbd716422c0c244d35f064f4fb4d3242915db8895d99bda4b7a4b
evidence_dir="$lane_root/evidence/r0_attempt_40_reproducibility"
backend_pattern="postgres: postgres $database 127.0.0.1"

test "$(sha256sum "$source_launcher" | awk '{print $1}')" = "$source_launcher_sha256"
test "$(sha256sum "$runner" | awk '{print $1}')" = "$runner_sha256"
test "$(sha256sum "$formal_contracts" | awk '{print $1}')" = "$formal_contracts_sha256"
test "$(sha256sum "$stability_contract" | awk '{print $1}')" = "$stability_contract_sha256"
test "$(sha256sum "$protocol" | awk '{print $1}')" = "$protocol_sha256"
test ! -e "$lane_root/baseline_r0_attempt_40"
test ! -e "$lane_root/runtime/baseline_r0_attempt_40.pid"
if pgrep -f '[r]un_pg_weighted_sql_baseline.py.*--port 64304' >/dev/null; then
    echo "owned TPC-H runner is still active" >&2
    exit 4
fi

mkdir -p "$evidence_dir"
container_id="$(docker inspect -f '{{.Id}}' "$container")"
blkio_root="/sys/fs/cgroup/blkio/docker/$container_id"
cat "$blkio_root/blkio.throttle.read_bps_device" > "$evidence_dir/blkio_before_cleanup_read.txt"
cat "$blkio_root/blkio.throttle.write_bps_device" > "$evidence_dir/blkio_before_cleanup_write.txt"

# Raise the physical cap before canceling attempt39's starved backends so
# cleanup itself cannot remain blocked behind the obsolete 15.8 KiB/s limit.
printf '259:3 262144\n' \
    | sudo tee "$blkio_root/blkio.throttle.read_bps_device" >/dev/null
printf '259:3 327680\n' \
    | sudo tee "$blkio_root/blkio.throttle.write_bps_device" >/dev/null
cat "$blkio_root/blkio.throttle.read_bps_device" > "$evidence_dir/blkio_applied_before_cleanup_read.txt"
cat "$blkio_root/blkio.throttle.write_bps_device" > "$evidence_dir/blkio_applied_before_cleanup_write.txt"
test "$(cat "$evidence_dir/blkio_applied_before_cleanup_read.txt")" = "259:3 262144"
test "$(cat "$evidence_dir/blkio_applied_before_cleanup_write.txt")" = "259:3 327680"

docker exec "$container" sh -c \
    "pids=\$(ps -eo pid=,args= | grep -F '$backend_pattern' | grep -v grep | awk '{print \$1}'); \
     if [ -n \"\$pids\" ]; then kill -TERM \$pids; fi"

backend_count=0
for _ in $(seq 1 150); do
    backend_count="$(docker exec "$container" sh -c \
        "ps -eo args= | grep -F '$backend_pattern' | grep -v grep | wc -l")"
    if [ "$backend_count" -eq 0 ]; then
        break
    fi
    sleep 2
done
test "$backend_count" -eq 0
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

ATTEMPT_ID=40 \
CPU_LIMIT_CORES=16 \
MEMORY_LIMIT_BYTES=2147483648 \
MEMORY_LIMIT_DOCKER=2048m \
IO_READ_CAP_BYTES_S=262144 \
IO_WRITE_CAP_BYTES_S=327680 \
IO_READ_CAP_MIB_S=0.25 \
IO_WRITE_CAP_MIB_S=0.3125 \
IO_TOTAL_CAP_MIB_S=0.5625 \
WORKERS=32 \
STATEMENT_TIMEOUT_MS=900000 \
exec "$source_launcher"
