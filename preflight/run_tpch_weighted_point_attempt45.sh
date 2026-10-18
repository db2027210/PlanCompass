#!/usr/bin/env bash
set -euo pipefail

lane=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpch_node04
attempt="$lane/experience_sgd/attempt45_tsgd_eta010_target5_v1"
point="$attempt/candidate_measurements/point_$(date +%Y%m%d_%H%M%S)_$$"
python_bin=/home/anonymous/recost_runtime_py311_24_11_1/bin/python3.11

mkdir -p "$attempt/candidate_measurements"
exec "$python_bin" -u "$attempt/code/shell/run_pg_weighted_sql_baseline.py" \
    --port 64304 \
    --database template_fullgrid_tpch_pg5 \
    --container pg5_tpch_s04_20260722 \
    --sql-dir "$lane/code/queries" \
    --output-dir "$point" \
    --workers 32 \
    --session-work-mem 1MB \
    --statement-timeout-ms 900000 \
    --cpu-limit-cores 28.8 \
    --memory-limit-bytes 2147483648 \
    --io-total-limit-mib-s 144 \
    --io-read-cap-mib-s 64 \
    --io-write-cap-mib-s 80 \
    --stability-target-resource memory
