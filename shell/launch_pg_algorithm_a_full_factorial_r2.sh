#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/20260816_algorithm_a_full_factorial_attemptfix_clean_r2
runner=${project_root}/shell/run_pg_algorithm_a_full_factorial_r2_lane.sh

if [[ ! -f "${campaign_root}/fresh_campaign_manifest.json" ]]; then
    echo "Algorithm A r2 campaign is not prepared: ${campaign_root}" >&2
    exit 1
fi
if pgrep -af 'run_pg_algorithm_[abc]_scene.py' >/tmp/pg_algorithm_a_r2_conflicting_controllers.txt; then
    echo "another PostgreSQL algorithm controller is active:" >&2
    sed -n '1,20p' /tmp/pg_algorithm_a_r2_conflicting_controllers.txt >&2
    exit 1
fi
for lane in job tpch xp_cpu xp_io; do
    session=pga_fullfactor_r2_${lane}
    if tmux has-session -t "${session}" 2>/dev/null; then
        echo "tmux session already exists: ${session}" >&2
        exit 1
    fi
done
for lane in job tpch xp_cpu xp_io; do
    tmux new-session -d -s "pga_fullfactor_r2_${lane}" -c "${project_root}" \
        "exec ${runner} ${lane}"
done
tmux list-sessions -F '#S' | sort | grep '^pga_fullfactor_r2_'
