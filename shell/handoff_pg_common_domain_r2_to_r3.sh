#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
r2_pid=${PG_COMMON_DOMAIN_R2_PID:-3567567}
r2_root=/disk1/resource_adaptive/pg_common_domain_frozen_champions_20260826/20260826_four_scene_common_domain_r2
r3_root=/disk1/resource_adaptive/pg_common_domain_frozen_champions_20260826/20260826_four_scene_common_domain_r3
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
runner=${project_root}/shell/run_pg_common_domain_frozen_champions.py

while kill -0 "${r2_pid}" 2>/dev/null; do
    sleep 20
done

if [[ ! -f "${r2_root}/clean_restore_receipt.json" ]]; then
    echo "r2 exited without a clean restore receipt; refusing r3 launch" >&2
    exit 1
fi

r2_phase=$(jq -r '.phase // ""' "${r2_root}/state.json")
if [[ "${r2_phase}" != "clean_stopped" ]]; then
    echo "r2 unexpected terminal phase=${r2_phase}; refusing r3 launch" >&2
    exit 1
fi

if pgrep -af '[2]0260826_four_scene_common_domain_r2/replays/' >/dev/null; then
    echo "r2 workload children still exist; refusing r3 launch" >&2
    exit 1
fi

# Restore the exact XP-I/O resource contract used by the A/B/C scene.
docker update \
    --cpuset-cpus '32-39,88-95,132-133,184-189,196-197,228-241' \
    --cpus 40 \
    --memory 24g \
    --memory-swap -1 \
    ra_fullgrid_hybench_none_20260718_w1 >/dev/null
docker update \
    --cpuset-cpus '40-47,96-103,134-135,190-195,198-199,242-255' \
    --cpus 40 \
    --memory 24g \
    --memory-swap -1 \
    ra_fullgrid_hybench_none_20260718_w2 >/dev/null

PYTHONPATH=${project_root} "${python_bin}" - <<'PY'
from shell.run_pg_common_domain_frozen_champions import (
    audit_container_resource_domain,
    scene_contracts,
)

receipt = audit_container_resource_domain(scene_contracts())
print(
    "container_resource_guard=passed "
    f"semantic_sha256={receipt['semantic_sha256']}"
)
PY

if [[ -e "${r3_root}" ]]; then
    echo "r3 root already exists; refusing to overwrite ${r3_root}" >&2
    exit 1
fi
mkdir -p "${r3_root}"

tmux -L pg_common_domain_20260826_r3 new-session -d \
    -s frozen_champions \
    -c "${project_root}" \
    "exec env PYTHONPATH=${project_root} PYTHONUNBUFFERED=1 CUDA_VISIBLE_DEVICES= BONECT_SHARED_SSD_COORDINATION_ENABLED=0 BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE=none BINARY_PLAN_TUNING_CPU_MEMORY_PARALLELISM=2 BINARY_PLAN_TUNING_IO_PARALLELISM=2 BINARY_PLAN_TUNING_PARALLEL_BASELINE=1 BINARY_PLAN_TUNING_PARALLEL_BASELINE_PARALLELISM=2 PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM=2 PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S=21600 PG_ALGORITHM_C_RESTORE_DATABASE_STRATEGY=FILE_COPY PG_ALGORITHM_C_RESTORE_MIN_HOST_AVAILABLE_GIB=128 PG_ALGORITHM_C_RESTORE_MIN_NUMA_FREE_GIB=64 PG_ALGORITHM_C_RESTORE_MEMORY_POLL_S=2 GENERIC_RUN_SECONDS=0 taskset -c 104-131,144-159 ${python_bin} -B -u ${runner} --output-dir ${r3_root} --seed 20260826 --max-stage-attempts 3 --maximum-baseline-drift-pct 10 >> ${r3_root}/controller.log 2>&1"

echo "launched r3 at $(date --iso-8601=seconds)"
