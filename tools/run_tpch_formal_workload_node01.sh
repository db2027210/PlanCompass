#!/usr/bin/env bash
set -euo pipefail

base=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
python_bin=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
container=pg5_tpch_ap_slot_node01_20260726
cpu_limit_cores=22
memory_limit_bytes=1730000000
io_read_cap_bytes_s=16777216
io_write_cap_bytes_s=20971520
io_read_cap_mib_s=16
io_write_cap_mib_s=20
io_total_cap_mib_s=36
point="$base/candidate_measurements/formal_point_$(date -u +%Y%m%dT%H%M%SZ)_$$"

unset LD_LIBRARY_PATH
mkdir -p "$base/candidate_measurements"

[[ "$(docker inspect -f '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' "$container")" \
    == "true 22000000000 1730000000 1730000000" ]]
container_id=$(docker inspect -f '{{.Id}}' "$container")
blkio_root="/sys/fs/cgroup/blkio/docker/$container_id"
test -d "$blkio_root"
awk -v cap="$io_read_cap_bytes_s" \
    'NF == 2 && $2 == cap { count += 1 } END { exit(count == 1 ? 0 : 1) }' \
    "$blkio_root/blkio.throttle.read_bps_device"
awk -v cap="$io_write_cap_bytes_s" \
    'NF == 2 && $2 == cap { count += 1 } END { exit(count == 1 ? 0 : 1) }' \
    "$blkio_root/blkio.throttle.write_bps_device"
docker exec "$container" pg_isready \
    -p 64304 -d template_fullgrid_tpch_pg5 -U postgres >/dev/null

exec "$python_bin" -u "$base/code/shell/run_pg_weighted_sql_baseline_profile_rolling_v2.py" \
    --port 63301 \
    --database template_fullgrid_tpch_pg5 \
    --container "$container" \
    --sql-dir "$base/code/queries" \
    --output-dir "$point" \
    --workers 36 \
    --warmup-seconds 180 \
    --session-work-mem 1MB \
    --statement-timeout-ms 900000 \
    --minimum-seconds 180 \
    --maximum-seconds 600 \
    --cpu-limit-cores "$cpu_limit_cores" \
    --memory-limit-bytes "$memory_limit_bytes" \
    --io-total-limit-mib-s "$io_total_cap_mib_s" \
    --io-read-cap-mib-s "$io_read_cap_mib_s" \
    --io-write-cap-mib-s "$io_write_cap_mib_s" \
    --r0-target-resource memory \
    --r0-target-min-pct 85 \
    --r0-target-max-pct 90 \
    --stability-target-resource memory
