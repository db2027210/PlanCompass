#!/usr/bin/env bash
set -euo pipefail

repo_root=/home/anonymous/program/resource_adaptive
local_root="$repo_root/pg4_relaunch_20260727"
host=anonymous@192.0.2.3
remote_root=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/source_sf10_migration_20260727
attempt=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt55_sf10_strict_r0_cpu7p5_20260727
session=pg_tpch_sf10_strict_r0_cpu7p5_20260727

while true; do
    if tar -C "$local_root" -cf - \
        run_tpch_sf10_strict_r0_point_node01.sh \
        run_tpch_sf10_strict_r0_supervisor_node01.sh \
        | ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" "
            set -e
            cd '$remote_root'
            tar -xf -
            chmod +x \
                run_tpch_sf10_strict_r0_point_node01.sh \
                run_tpch_sf10_strict_r0_supervisor_node01.sh
            test \"\$(sha256sum run_tpch_sf10_strict_r0_point_node01.sh | cut -d' ' -f1)\" = \
                '2cca4032c253514877e792354cb6d19aa6da87ebd415a247f00c2d488fe404f2'
            test \"\$(sha256sum run_tpch_sf10_strict_r0_supervisor_node01.sh | cut -d' ' -f1)\" = \
                'f332ba227986143c58c44c409085629ed31cf3234b1d75758936ed0708ae0162'
            tmux has-session -t '$session' 2>/dev/null \
                || tmux new-session -d -s '$session' \
                    \"cd '$remote_root' && flock -n /tmp/pg_tpch_sf10_strict_r0_20260727.lock bash run_tpch_sf10_strict_r0_supervisor_node01.sh\"
            for ignored in 1 2 3 4 5; do
                test -s '$attempt/r0_supervisor_state.json' && exit 0
                sleep 2
            done
            exit 1
        "; then
        printf 'TPC-H CPU7.5 attempt55 deployed and started\n'
        exit 0
    fi
    sleep 30
done
