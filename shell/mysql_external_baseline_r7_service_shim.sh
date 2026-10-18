#!/usr/bin/env bash
set -euo pipefail

# The transient user service owns this shim only.  The formal GPTuner ->
# AQETuner controller runs in a separate session so cleanup of the caller's
# service unit cannot propagate SIGTERM into a physical workload wave.
controller=/home/anonymous/program/resource_adaptive/shell/resume_mysql_external_baseline_r7.sh

setsid /bin/bash "${controller}" &
controller_pid=$!
echo "$(date --iso-8601=seconds) detached_controller_pid=${controller_pid}" 
wait "${controller_pid}"
