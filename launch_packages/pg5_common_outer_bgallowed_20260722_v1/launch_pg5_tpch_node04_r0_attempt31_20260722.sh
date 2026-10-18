#!/usr/bin/env bash
set -euo pipefail

lane_root=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpch_node04
source_launcher="$lane_root/launch_pg5_tpch_node04_r0_attempt29_20260722.sh"
source_launcher_sha256=a1af1c8d776bb7323d2c4a459c8789b209cd307e06b760098fad8e14a7535e2f

test "$(sha256sum "$source_launcher" | awk '{print $1}')" = "$source_launcher_sha256"
test ! -e "$lane_root/baseline_r0_attempt_31"
test ! -e "$lane_root/runtime/baseline_r0_attempt_31.pid"
if pgrep -f '[r]un_pg_weighted_sql_baseline.py.*--port 64304' >/dev/null; then
    echo "owned TPC-H runner is still active" >&2
    exit 4
fi

ATTEMPT_ID=31 \
CPU_LIMIT_CORES=7.138834783 \
MEMORY_LIMIT_BYTES=2147483648 \
MEMORY_LIMIT_DOCKER=2048m \
IO_READ_CAP_BYTES_S=13312 \
IO_WRITE_CAP_BYTES_S=13312 \
IO_READ_CAP_MIB_S=0.0126953125 \
IO_WRITE_CAP_MIB_S=0.0126953125 \
IO_TOTAL_CAP_MIB_S=0.025390625 \
WORKERS=32 \
exec "$source_launcher"
