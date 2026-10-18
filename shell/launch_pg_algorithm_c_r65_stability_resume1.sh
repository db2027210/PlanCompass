#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
runner=${project_root}/shell/run_pg_algorithm_c_r65_stability_resume1_lane.sh

for lane in job tpch xp_cpu xp_io; do
    session=pgc_r65_stability_resume1_${lane}
    if tmux has-session -t "${session}" 2>/dev/null; then
        echo "tmux session already exists: ${session}" >&2
        exit 1
    fi
done

for lane in job tpch xp_cpu xp_io; do
    tmux new-session -d -s "pgc_r65_stability_resume1_${lane}" \
        -c "${project_root}" "exec ${runner} ${lane}"
done

tmux list-sessions -F '#S' | sort | grep '^pgc_r65_stability_resume1_'
