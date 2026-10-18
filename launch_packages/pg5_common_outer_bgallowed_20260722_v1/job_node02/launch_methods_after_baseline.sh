#!/usr/bin/env bash
set -euo pipefail

LANE_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02
BASELINE_RESULT=${LANE_ROOT}/history/job/baseline/R0/attempt_1/point_result.json
COMMON_DRIVER=${LANE_ROOT}/workspace/formal_pg_common_outer.py
PYTHON=${LANE_ROOT}/runtime/python/bin/python

if ! test -s "${BASELINE_RESULT}"; then
    echo "BLOCKED: shared JOB baseline R0 is not complete: ${BASELINE_RESULT}" >&2
    exit 20
fi
if ! "${PYTHON}" -c 'import json,sys; value=json.load(open(sys.argv[1])); raise SystemExit(0 if value.get("status")=="complete" else 1)' "${BASELINE_RESULT}"; then
    echo "BLOCKED: shared JOB baseline R0 did not complete successfully" >&2
    exit 21
fi
if ! test -s "${COMMON_DRIVER}"; then
    echo "BLOCKED: checksum-bound common-outer driver is absent: ${COMMON_DRIVER}" >&2
    echo "The retired p24 10-candidate optimizer is intentionally not substituted." >&2
    exit 22
fi

exec "${PYTHON}" -u "${COMMON_DRIVER}" \
    --campaign-id pg5_common_outer_bgallowed_20260722_v1 \
    --lane-manifest "${LANE_ROOT}/manifest/lane_manifest.json" \
    --workload-manifest "${LANE_ROOT}/manifest/workload_manifest.json" \
    --baseline-result "${BASELINE_RESULT}" \
    --methods experience_table_all_subset_sgd,random,brute \
    --sgd-all-subset-count 127 \
    --sgd-first-round-real-replays 5 \
    --accepted-shrink-target 10 \
    --shrink-only-after-acceptance
