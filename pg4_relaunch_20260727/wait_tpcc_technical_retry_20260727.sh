#!/usr/bin/env bash
set -euo pipefail

unit=pg4-tpcc-attempt15-cgroup-sealed.service
output_dir=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt15_direction_retrain_cgroup_sealed_20260727/calibration
log_path=history/pg4_four_experiments_live_20260727/tpcc_technical_retry.log

while systemctl --user is-active --quiet "$unit"; do
    sleep 30
done

# Avoid racing a unit transition where the main process is still being reaped.
while pgrep -f "shell/run_tpcc_direction_calibration.py --output-dir $output_dir" >/dev/null; do
    sleep 5
done

exec /home/anonymous/miniconda3/bin/python3 -u \
    shell/run_tpcc_direction_calibration.py \
    --output-dir "$output_dir" \
    --blocks 3 \
    --max-attempts 3 \
    --resume \
    --retry-technical-failures \
    --cpu-limit-cores 3.85 \
    --memory-limit-bytes 4294967296 \
    --io-total-limit-mib-s 54 \
    --io-read-cap-mib-s 27 \
    --io-write-cap-mib-s 27 \
    >"$log_path" 2>&1
