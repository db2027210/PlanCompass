#!/usr/bin/env bash
set -euo pipefail

host=anonymous@192.0.2.3
session=pg_tpch_sf10_strict_r0_cpu7p1_20260727
source_root=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/source_sf10_migration_20260727
state_path=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt54_sf10_strict_r0_cpu7p1_20260727/r0_supervisor_state.json

while true; do
    if ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" \
        "tmux has-session -t '$session' 2>/dev/null || tmux new-session -d -s '$session' 'cd $source_root && flock -n /tmp/pg_tpch_sf10_strict_r0_20260727.lock bash run_tpch_sf10_strict_r0_supervisor_node01.sh'; test -s '$state_path'"; then
        printf 'TPC-H CPU7.1 strict R0 launched: %s\n' "$state_path"
        exit 0
    fi
    sleep 30
done
