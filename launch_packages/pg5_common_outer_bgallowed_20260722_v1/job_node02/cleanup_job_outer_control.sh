#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
CONTAINER=ra_pg5_job_node02_v1_outercontrol
CONTROL_ROOT=${RA_PG_CONTROL_ROOT:-${LANE_ROOT}/work/control_outer_attempt1}

if docker inspect "${CONTAINER}" >/dev/null 2>&1; then
    docker rm -f "${CONTAINER}" >/dev/null
fi
if [[ -d "${CONTROL_ROOT}" ]]; then
    docker run --rm --network none \
        --volume "${CONTROL_ROOT}:/lane-control" \
        --entrypoint sh postgres:17 \
        -c 'find /lane-control -mindepth 1 -depth -delete' >/dev/null
    rmdir "${CONTROL_ROOT}"
fi
