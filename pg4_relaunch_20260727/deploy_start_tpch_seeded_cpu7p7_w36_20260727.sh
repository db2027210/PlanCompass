#!/usr/bin/env bash
set -euo pipefail

repo_root=/home/anonymous/program/resource_adaptive
local_root="$repo_root/pg4_relaunch_20260727"
host=anonymous@192.0.2.3
remote_root=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/source_sf10_migration_20260727
attempt=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt57_sf10_strict_r0_seeded_cpu7p7_w36_20260727
session=pg_tpch_sf10_strict_r0_seeded_cpu7p7_w36_20260727

while true; do
    if tar -C "$local_root" -cf - \
        run_pg_weighted_sql_baseline_seeded.py \
        run_tpch_sf10_strict_r0_point_node01.sh \
        run_tpch_sf10_strict_r0_supervisor_node01.sh \
        | ssh -o BatchMode=yes -o ConnectTimeout=20 "$host" "
            set -e
            cd '$remote_root'
            tar -xf -
            chmod +x \
                run_pg_weighted_sql_baseline_seeded.py \
                run_tpch_sf10_strict_r0_point_node01.sh \
                run_tpch_sf10_strict_r0_supervisor_node01.sh
            test \"\$(sha256sum run_pg_weighted_sql_baseline_seeded.py | cut -d' ' -f1)\" = \
                '7b1c936dee0220ef8973f855fcece73ee73eab764e53d847deaaa1e52ac9b96e'
            test \"\$(sha256sum run_tpch_sf10_strict_r0_point_node01.sh | cut -d' ' -f1)\" = \
                '2c3da011af8c2af7903fad4e98dbe6c636582c8fcd279072ad6b2f2dae8b17df'
            test \"\$(sha256sum run_tpch_sf10_strict_r0_supervisor_node01.sh | cut -d' ' -f1)\" = \
                '9430e99d0ddbe1cd6bd2bb31922ca56082c2c41121c9bbb3a7eba0130cf8a43c'
            tmux has-session -t '$session' 2>/dev/null \
                || tmux new-session -d -s '$session' \
                    \"cd '$remote_root' && flock -n /tmp/pg_tpch_sf10_strict_r0_20260727.lock bash run_tpch_sf10_strict_r0_supervisor_node01.sh\"
            for ignored in 1 2 3 4 5; do
                test -s '$attempt/r0_supervisor_state.json' && exit 0
                sleep 2
            done
            exit 1
        "; then
        printf 'TPC-H seeded CPU7.7 workers36 attempt57 deployed and started\n'
        exit 0
    fi
    sleep 30
done
