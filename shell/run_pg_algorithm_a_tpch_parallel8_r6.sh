#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/20260816_algorithm_a_tpch_parallel8_clean_r6
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
controller=${project_root}/shell/run_pg_algorithm_a_scene.py
output_dir=${campaign_root}/tpch_memory
session=pga_fullfactor_r6_tpch8

if [[ ! -f "${campaign_root}/fresh_campaign_manifest.json" ]]; then
    echo "TPC-H parallel-8 r6 campaign is not prepared: ${campaign_root}" >&2
    exit 1
fi
if pgrep -af 'run_pg_algorithm_a_scene.py.*tpch_memory' >/tmp/pg_algorithm_a_r6_tpch_conflict.txt; then
    echo "another Algorithm A TPC-H controller is active:" >&2
    sed -n '1,20p' /tmp/pg_algorithm_a_r6_tpch_conflict.txt >&2
    exit 1
fi
if tmux has-session -t "${session}" 2>/dev/null; then
    echo "tmux session already exists: ${session}" >&2
    exit 1
fi

tmux new-session -d -s "${session}" -c "${project_root}" \
    "export PYTHONUNBUFFERED=1 PYTHONPATH=${project_root}; \
     export BONECT_SHARED_SSD_COORDINATION_ENABLED=0 BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE=none; \
     export BONECT_ALGORITHM_C_BASELINE_COHORT_ID=20260816_algorithm_a_tpch_parallel8_r6; \
     export BONECT_ALGORITHM_C_BASELINE_COHORT_SIZE=1 BONECT_ALGORITHM_C_BASELINE_COHORT_TIMEOUT_S=1800; \
     export BONECT_ALGORITHM_C_CANDIDATE_COHORT_SIZE=1 BONECT_ALGORITHM_C_CANDIDATE_COHORT_TIMEOUT_S=1800; \
     export PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S=21600; \
     export GENERIC_RUN_SECONDS=0 GENERIC_WORKLOAD=tpch; \
     export BENCHBASE_SOURCE_ROOT=/home/anonymous/benchbase-postgres; \
     export BENCHBASE_RUNTIME_ROOT=${campaign_root}/benchbase_runtime; \
     export BENCHBASE_SCALEFACTOR=5 BENCHBASE_TPCH_WORKERS=8; \
     export BENCHBASE_JAVA_XMX=-Xmx2g TPCH_TARGET_WALL_TIME_S=900; \
     unset BONECT_ALGORITHM_C_CANDIDATE_COHORT_ID; \
     exec taskset -c 184-199 ${python_bin} -B -u ${controller} \
       --config ${output_dir}/optimizer_config.json \
       --output-dir ${output_dir} --target memory \
       >> ${output_dir}/controller.log 2>&1"

tmux list-sessions -F '#S' | sort | grep '^pga_fullfactor_r6_tpch8$'
