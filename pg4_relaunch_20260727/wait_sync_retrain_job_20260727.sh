#!/usr/bin/env bash
set -euo pipefail

project=/home/anonymous/program/resource_adaptive
remote=anonymous@192.0.2.4
remote_pid=5922
remote_attempt=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_8e_codex_sealed_r0_20260727
local_root="$project/pg4_relaunch_20260727/retraining/job"
endpoint="$local_root/endpoint_calibration_attempt8e_20260727"

# Keep one SSH channel instead of creating a new probe connection every minute.
ssh -o BatchMode=yes "$remote" \
    "while kill -0 $remote_pid 2>/dev/null; do sleep 60; done"

mkdir -p "$local_root"
if [[ -e "$endpoint" ]]; then
    echo "refusing to overwrite existing endpoint evidence: $endpoint" >&2
    exit 44
fi
scp -p -r \
    "$remote:$remote_attempt/direction_calibration_endpoints" \
    "$endpoint"

record_count=$(
    find "$endpoint" -type f -name fresh_model_calibration.jsonl \
        -exec cat {} + | wc -l
)
if [[ "$record_count" -ne 42 ]]; then
    echo "endpoint calibration incomplete: $record_count/42" >&2
    exit 43
fi

exec bash "$project/pg4_relaunch_20260727/run_job_retrain_20260727.sh"
