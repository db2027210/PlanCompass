#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
READY=${LANE_ROOT}/source/jobdata/checksums.sha256
PID_FILE=${LANE_ROOT}/lease/bootstrap_supervisor.pid
printf '%s\n' "$$" >"${PID_FILE}"
echo "WAIT_DATASET_START pid=$$ at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
for _ in $(seq 1 720); do
    if test -s "${READY}"; then
        if (cd "${LANE_ROOT}/source/jobdata" && sha256sum -c checksums.sha256); then
            echo "WAIT_DATASET_COMPLETE at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            exec "${LANE_ROOT}/launch/bootstrap_and_launch.sh"
        fi
        echo "BLOCKED: dataset checksum verification failed" >&2
        exit 41
    fi
    sleep 5
done
echo "BLOCKED: dataset transfer did not publish checksums within 3600 seconds" >&2
exit 42
