#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
r1_pid=${PG_COMMON_DOMAIN_R1_PID:-3519268}
r1_root=/disk1/resource_adaptive/pg_common_domain_frozen_champions_20260826/20260826_four_scene_common_domain_r1
r2_root=/disk1/resource_adaptive/pg_common_domain_frozen_champions_20260826/20260826_four_scene_common_domain_r2
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
runner=${project_root}/shell/run_pg_common_domain_frozen_champions.py

while kill -0 "${r1_pid}" 2>/dev/null; do
    sleep 20
done

if [[ ! -f "${r1_root}/clean_restore_receipt.json" ]]; then
    echo "r1 exited without a clean restore receipt; refusing automatic r2 launch" >&2
    exit 1
fi

r1_phase=$(jq -r '.phase // ""' "${r1_root}/state.json")
if [[ "${r1_phase}" != "failed_closed" && "${r1_phase}" != "clean_stopped" ]]; then
    echo "r1 unexpected terminal phase=${r1_phase}; refusing automatic r2 launch" >&2
    exit 1
fi

if pgrep -af 'common_common_baseline_start_a1_' >/dev/null; then
    echo "r1 workload children still exist; refusing automatic r2 launch" >&2
    exit 1
fi

mkdir -p "${r2_root}"
tmux -L pg_common_domain_20260826_r2 new-session -d \
    -s frozen_champions \
    -c "${project_root}" \
    "exec env PYTHONPATH=${project_root} PYTHONUNBUFFERED=1 CUDA_VISIBLE_DEVICES= BONECT_SHARED_SSD_COORDINATION_ENABLED=0 BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE=none BINARY_PLAN_TUNING_CPU_MEMORY_PARALLELISM=2 BINARY_PLAN_TUNING_IO_PARALLELISM=2 BINARY_PLAN_TUNING_PARALLEL_BASELINE=1 BINARY_PLAN_TUNING_PARALLEL_BASELINE_PARALLELISM=2 PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM=2 PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S=21600 PG_ALGORITHM_C_RESTORE_DATABASE_STRATEGY=FILE_COPY PG_ALGORITHM_C_RESTORE_MIN_HOST_AVAILABLE_GIB=128 PG_ALGORITHM_C_RESTORE_MIN_NUMA_FREE_GIB=64 PG_ALGORITHM_C_RESTORE_MEMORY_POLL_S=2 GENERIC_RUN_SECONDS=0 taskset -c 104-131,144-159 ${python_bin} -B -u ${runner} --output-dir ${r2_root} --seed 20260826 --max-stage-attempts 3 --maximum-baseline-drift-pct 10 >> ${r2_root}/controller.log 2>&1"

echo "launched r2 at $(date --iso-8601=seconds)"
