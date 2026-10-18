#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
P=${B}/job_round8_corrected_top5_v6_20260731
F=${B}/formal10_round8_autonomous_batch06_repeat_gate_v31_20260731T1850
PACKAGE=${F}/package
SELECTION=${PACKAGE}/top5_promoted_formal.json
GATE=${PACKAGE}/entry_gate.json
STATE=${P}/authoritative_formal_state_snapshot.json
DEPENDENCY=${P}/job_round8_strict_replay_contract.py
TARGET_DEPENDENCY=${PACKAGE}/job_round8_strict_replay_contract.py

EXPECTED_SELECTION_SHA=7a96b17ea0060af080dbe2720833161374344fa0d466b2fe28b0e9402387096e
EXPECTED_GATE_SHA=2021a8a9a5adbe5d22f9e4afb3c8aaf13ef229cd82e7faa426984077137ec33a
EXPECTED_STATE_SHA=430e874c54603aff85304d8e4f52e28d4d6784236978e5e6b33962403c87c2a6
EXPECTED_CONTROLLER_SHA=0181f503eb601ff42e37ac188b6e71cbb70b367a35a2300c05bda778a911a164
EXPECTED_RUNNER_SHA=9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
EXPECTED_DEPENDENCY_SHA=a5ae7fddcfcd16e1715e35cc5a67c3d3d135b2542ff0e132a4cca5817afddf05

test "$(sha256sum "${SELECTION}" | awk '{print $1}')" = "${EXPECTED_SELECTION_SHA}"
test "$(sha256sum "${GATE}" | awk '{print $1}')" = "${EXPECTED_GATE_SHA}"
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
test "$(sha256sum "${PACKAGE}/job_formal_target10_batch_controller.py" | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA}"
test "$(sha256sum "${PACKAGE}/run_job_round8_strict_serial.py" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${DEPENDENCY}" | awk '{print $1}')" = "${EXPECTED_DEPENDENCY_SHA}"
test ! -e "${F}/replay"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = 321
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = 181
test "$(tr -d '[:space:]' < "${B}/formal_search_accepted_counter.txt")" = 2
jq -e '.passed == true and .formal_accepted_total == 2' "${GATE}" >/dev/null

cp "${DEPENDENCY}" "${TARGET_DEPENDENCY}"
test "$(sha256sum "${TARGET_DEPENDENCY}" | awk '{print $1}')" = "${EXPECTED_DEPENDENCY_SHA}"
sha256sum \
    "${SELECTION}" "${GATE}" "${STATE}" \
    "${PACKAGE}/job_formal_target10_batch_controller.py" \
    "${PACKAGE}/run_job_round8_strict_serial.py" \
    "${TARGET_DEPENDENCY}" > "${PACKAGE}/sealed_restart_v32.sha256"

cd "${PACKAGE}"
exec python3 -u "${PACKAGE}/job_formal_target10_batch_controller.py" \
    --selection "${SELECTION}" \
    --selection-sha256 "${EXPECTED_SELECTION_SHA}" \
    --gate "${GATE}" \
    --gate-sha256 "${EXPECTED_GATE_SHA}" \
    --state "${STATE}" \
    --state-sha256 "${EXPECTED_STATE_SHA}" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --replay-root "${F}/replay" \
    --global-counter "${B}/replay_counter.txt" \
    --formal-replay-counter "${B}/formal_search_replay_counter.txt" \
    --accepted-counter "${B}/formal_search_accepted_counter.txt"
