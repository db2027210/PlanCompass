#!/usr/bin/env bash
set -euo pipefail

lane_root=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpch_node04
container=pg5_tpch_s04_20260722
base="$lane_root/launch_pg5_tpch_node04_r0_v5_base_20260723.sh"
protocol="$lane_root/tpch_node04_attempt43_reproducibility_protocol.json"
test "$(sha256sum "$base" | awk '{print $1}')" = "${ATTEMPT43_BASE_SHA256:?}"
test "$(sha256sum "$protocol" | awk '{print $1}')" = "${ATTEMPT43_PROTOCOL_SHA256:?}"
test ! -e "$lane_root/baseline_r0_attempt_43"
test ! -e "$lane_root/runtime/baseline_r0_attempt_43.pid"
! pgrep -f '[r]un_pg_weighted_sql_baseline.py.*--port 64304' >/dev/null

docker restart --time 60 "$container" >/dev/null
for _ in $(seq 1 120); do
    docker exec "$container" pg_isready -p 64304 -d template_fullgrid_tpch_pg5 \
        -U postgres >/dev/null 2>&1 && break
    sleep 1
done
docker exec "$container" pg_isready -p 64304 -d template_fullgrid_tpch_pg5 -U postgres >/dev/null

ATTEMPT_ID=43 \
CPU_LIMIT_CORES=28.8 \
MEMORY_LIMIT_BYTES=2147483648 \
MEMORY_LIMIT_DOCKER=2048m \
IO_READ_CAP_BYTES_S=67108864 \
IO_WRITE_CAP_BYTES_S=83886080 \
IO_READ_CAP_MIB_S=64 \
IO_WRITE_CAP_MIB_S=80 \
IO_TOTAL_CAP_MIB_S=144 \
WORKERS=32 \
STATEMENT_TIMEOUT_MS=900000 \
exec "$base"
