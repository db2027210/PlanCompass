#!/usr/bin/env bash
set -euo pipefail

controller=/home/anonymous/program/resource_adaptive/shell/resume_mysql_external_baseline_disk1_r3.sh
log=/disk1/resource_adaptive/mysql_external_baselines_20260826/20260826_gptuner_aqetuner_four_disk1_r3.resume.log

setsid /bin/bash "${controller}" >>"${log}" 2>&1 &
controller_pid=$!
echo "$(date --iso-8601=seconds) detached_controller_pid=${controller_pid}"
wait "${controller_pid}"
