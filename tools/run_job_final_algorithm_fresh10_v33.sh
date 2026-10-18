#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
P=${B}/job_round8_corrected_top5_v6_20260731
TARGET=${B}/formal10_final_algorithm_fresh10_v33_20260731T1910
PACKAGE=${TARGET}/package
STATE=${PACKAGE}/formal_state.json
GATE=${PACKAGE}/entry_gate.json
SELECTION=${PACKAGE}/top5_promoted_formal.json
REPLAY=${TARGET}/replay
GLOBAL_COUNTER=${B}/replay_counter.txt
FORMAL_REPLAY_COUNTER=${B}/formal_search_replay_counter.txt
ACCEPTED_COUNTER=${PACKAGE}/formal_search_accepted_counter.txt

EXPECTED_STATE_SHA=59a76376952a792a7fb41cdb440793744f6975454f9fceabab96140d37b89f23
EXPECTED_GATE_SHA=6b352cc68caf7214a03e1a7e77f188261465fc5458b11f8aa15652f658a487c0
EXPECTED_SELECTION_SHA=7a96b17ea0060af080dbe2720833161374344fa0d466b2fe28b0e9402387096e
EXPECTED_CONTROLLER_SHA=df512fdecaa2d98f23144991a75ed4eb68c32ec5cc143ff9d9bfb1232f179356
EXPECTED_RUNNER_SHA=9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
EXPECTED_DEPENDENCY_SHA=a5ae7fddcfcd16e1715e35cc5a67c3d3d135b2542ff0e132a4cca5817afddf05

test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
test "$(sha256sum "${GATE}" | awk '{print $1}')" = "${EXPECTED_GATE_SHA}"
test "$(sha256sum "${SELECTION}" | awk '{print $1}')" = "${EXPECTED_SELECTION_SHA}"
test "$(sha256sum "${PACKAGE}/job_formal_target10_batch_controller.py" | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA}"
test "$(sha256sum "${PACKAGE}/run_job_round8_strict_serial.py" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${PACKAGE}/job_round8_strict_replay_contract.py" | awk '{print $1}')" = "${EXPECTED_DEPENDENCY_SHA}"
test ! -e "${REPLAY}"
test "$(tr -d '[:space:]' < "${GLOBAL_COUNTER}")" = 321
test "$(tr -d '[:space:]' < "${FORMAL_REPLAY_COUNTER}")" = 181
test "$(tr -d '[:space:]' < "${ACCEPTED_COUNTER}")" = 0

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
    --replay-root "${REPLAY}" \
    --global-counter "${GLOBAL_COUNTER}" \
    --formal-replay-counter "${FORMAL_REPLAY_COUNTER}" \
    --accepted-counter "${ACCEPTED_COUNTER}"
