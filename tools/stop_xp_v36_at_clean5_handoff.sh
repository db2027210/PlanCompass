#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
CAMPAIGN=${PROJECT}/history/xp_final_algorithm_fresh10_v36_20260803
STATE=${CAMPAIGN}/state.json
SUMMARY=${CAMPAIGN}/batches/cont001/replay_summary.json
CONTROLLER_PATTERN="resume_xp_fresh0_fixed_contract_target10_v36.py.*--campaign-root ${CAMPAIGN}"

jq -e '
    .physical_workload_invocation_count == 5 and
    .successful_real_replay_count == 5 and
    .technical_failure_count == 0 and
    .baseline_restore_verified == true and
    .accepted_count_delta == 0
' "${SUMMARY}" >/dev/null
jq -e '
    .accepted_count == 0 and .accepted_target == 10 and
    .next_batch_index == 2 and .last_batch.batch_id == "cont001" and
    .last_batch.accepted == 0 and .last_batch.technical_failures == 0 and
    .gradient_state.update_count == 0
' "${STATE}" >/dev/null
if find "${CAMPAIGN}/batches/cont002" -type f -name workload.log -size +0c -print -quit 2>/dev/null | grep -q .; then
    echo "XP v36 cont002 workload evidence exists; refusing zero-call handoff" >&2
    exit 75
fi

controller_pid="$(pgrep -f "${CONTROLLER_PATTERN}" | head -1 || true)"
if [[ -z "${controller_pid}" ]]; then
    echo "XP v36 controller is already stopped; zero-call boundary is clean"
    exit 0
fi
if [[ "$(ps -o stat= -p "${controller_pid}" | tr -d ' ')" == Z* ]]; then
    echo "XP v36 controller is a zombie; zero-call boundary is clean"
    exit 0
fi

kill -STOP "${controller_pid}"
descendants="$(pgrep -P "${controller_pid}" || true)"
if [[ -n "${descendants}" ]]; then
    echo "XP v36 acquired a child during zero-call handoff; leaving controller stopped" >&2
    exit 76
fi
if find "${CAMPAIGN}/batches/cont002" -type f -name workload.log -size +0c -print -quit 2>/dev/null | grep -q .; then
    echo "XP v36 cont002 workload appeared during handoff; leaving controller stopped" >&2
    exit 77
fi
kill -TERM "${controller_pid}"
kill -CONT "${controller_pid}"
for _ in {1..100}; do
    if ! kill -0 "${controller_pid}" 2>/dev/null; then
        echo "XP v36 stopped after clean cont001 and before any cont002 workload"
        exit 0
    fi
    sleep 0.1
done
echo "XP v36 controller did not terminate after zero-call handoff" >&2
exit 78
