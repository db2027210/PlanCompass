#!/usr/bin/env bash
set -euo pipefail

controller=/home/anonymous/program/resource_adaptive/shell/run_mysql_external_baseline_disk1_r5_pipeline.sh
log=/disk1/resource_adaptive/mysql_external_baselines_20260826/20260826_gptuner_aqetuner_four_disk1_r5_affinity_replacement_clean.log

setsid /bin/bash "${controller}" >>"${log}" 2>&1 &
controller_pid=$!
echo "$(date --iso-8601=seconds) detached_controller_pid=${controller_pid}"
wait "${controller_pid}"
