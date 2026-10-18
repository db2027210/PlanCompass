#!/usr/bin/env bash
set -euo pipefail

# Unattended JOB target10 chain: finish the sealed successor004 diagnostics and
# formal batch004, then hand off to the idempotent autonomous successor loop.
# Every child independently pins state/code hashes and enforces max-five real
# calls per diagnostic/formal batch.
PROBE=/tmp/launch_job_fresh10_v45_diagnostic_probe004.sh
PROMOTE=/tmp/watch_job_fresh10_v46_repeat_gate_formal004.sh
TARGET10=/tmp/job_fresh10_target10_autonomous_v49.py
PROBE_AUDIT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726/job_final_algorithm_fresh10_v36_20260731T2020/autonomous_v45/diagnostic_probe004/completion_audit.json
FORMAL004_TRANSACTION=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726/job_final_algorithm_fresh10_v36_20260731T2020/autonomous_v45/formal_batch004/replay/formal_state_commit_transaction.json

EXPECTED_PROBE_SHA=986bc7400e4c51b9170e9552bd89c7a18e5060775798eb80a876afdc23ef5036
# Updated after the state-pin correction and three-observation median formal
# promotion were sealed in the v46 launcher.
EXPECTED_PROMOTE_SHA=25d8f8f2d6eeea84845548a2e315a14b808e78e123960638020957ed69092b4d
EXPECTED_TARGET10_SHA=3688f00b0a32c490f5dd3781b26642f48361c3ba541256f9855730284016035c

test "$(sha256sum "${PROBE}" | awk '{print $1}')" = "${EXPECTED_PROBE_SHA}"
test "$(sha256sum "${PROMOTE}" | awk '{print $1}')" = "${EXPECTED_PROMOTE_SHA}"
test "$(sha256sum "${TARGET10}" | awk '{print $1}')" = "${EXPECTED_TARGET10_SHA}"

# Idempotent handoff: a clean, sealed probe004 may already have been started
# independently.  The v46 launcher revalidates its summary, state neutrality,
# and pinned formal-state SHA before doing any further workload call.
if ! test -f "${PROBE_AUDIT}"; then
  bash "${PROBE}"
fi
if test -f "${FORMAL004_TRANSACTION}"; then
  jq -e '.status == "committed"' "${FORMAL004_TRANSACTION}" >/dev/null
else
  bash "${PROMOTE}"
fi
exec python3 -u "${TARGET10}"
