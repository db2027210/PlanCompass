#!/usr/bin/env bash
set -euo pipefail

lane_root=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpch_node04
container=pg5_tpch_s04_20260722
source_launcher="$lane_root/launch_pg5_tpch_node04_r0_v4b_base_20260723.sh"
protocol="$lane_root/tpch_node04_attempt41_reproducibility_protocol.json"
protocol_sha256="${ATTEMPT41_PROTOCOL_SHA256:?ATTEMPT41_PROTOCOL_SHA256 is required}"
evidence_dir="$lane_root/evidence/r0_attempt_41_reproducibility"

test "$(sha256sum "$source_launcher" | awk '{print $1}')" = \
  b3ac1c6b1dca1b2ed54aaf573cbc154255017bd8b0aff2f7e00e203c445b8761
test "$(sha256sum "$protocol" | awk '{print $1}')" = "$protocol_sha256"
test ! -e "$lane_root/baseline_r0_attempt_41"
test ! -e "$lane_root/runtime/baseline_r0_attempt_41.pid"
if pgrep -f '[r]un_pg_weighted_sql_baseline.py.*--port 64304' >/dev/null; then
    echo "owned TPC-H runner is still active" >&2
    exit 4
fi

mkdir -p "$evidence_dir"
docker inspect "$container" > "$evidence_dir/container_before_restart.json"
docker restart --time 60 "$container" > "$evidence_dir/container_restart.txt"
for _ in $(seq 1 120); do
    if docker exec "$container" pg_isready -p 64304 \
        -d template_fullgrid_tpch_pg5 -U postgres >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker exec "$container" pg_isready -p 64304 \
    -d template_fullgrid_tpch_pg5 -U postgres > "$evidence_dir/pg_isready.txt"

ATTEMPT_ID=41 \
CPU_LIMIT_CORES=16 \
MEMORY_LIMIT_BYTES=2147483648 \
MEMORY_LIMIT_DOCKER=2048m \
IO_READ_CAP_BYTES_S=67108864 \
IO_WRITE_CAP_BYTES_S=83886080 \
IO_READ_CAP_MIB_S=64 \
IO_WRITE_CAP_MIB_S=80 \
IO_TOTAL_CAP_MIB_S=144 \
WORKERS=32 \
STATEMENT_TIMEOUT_MS=900000 \
exec "$source_launcher"
