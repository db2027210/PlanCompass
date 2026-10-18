#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
P=${B}/job_round8_corrected_top5_v6_20260731
SOURCE=${B}/formal10_round8_autonomous_batch06_repeat_gate_v31_20260731T1850
TARGET=${B}/formal10_final_algorithm_fresh10_v33_20260731T1910
PACKAGE=${TARGET}/package
REPLAY=${TARGET}/replay
PREP=/tmp/prepare_job_fresh_formal_campaign_v33.py
CONTROLLER_SOURCE=/tmp/job_formal_target10_batch_controller_v33.py
SOURCE_STATE=${P}/authoritative_formal_state_snapshot.json
SOURCE_GATE=${SOURCE}/package/entry_gate.json
SOURCE_SELECTION=${SOURCE}/package/top5_promoted_formal.json
FAILED_SUMMARY=${SOURCE}/replay/summary.json
GLOBAL_COUNTER=${B}/replay_counter.txt
FORMAL_REPLAY_COUNTER=${B}/formal_search_replay_counter.txt
OLD_ACCEPTED_COUNTER=${B}/formal_search_accepted_counter.txt
NEW_ACCEPTED_COUNTER=${PACKAGE}/formal_search_accepted_counter.txt
PREP_STDOUT=/tmp/job_fresh_formal_v33_prepare_stdout.json

EXPECTED_PREP_SHA=a61cf575d2b3a61902b64d1096b25d1281fa2c8a42b72394c3a8f28104fdd7be
EXPECTED_CONTROLLER_SHA=df512fdecaa2d98f23144991a75ed4eb68c32ec5cc143ff9d9bfb1232f179356
EXPECTED_STATE_SHA=430e874c54603aff85304d8e4f52e28d4d6784236978e5e6b33962403c87c2a6
EXPECTED_GATE_SHA=2021a8a9a5adbe5d22f9e4afb3c8aaf13ef229cd82e7faa426984077137ec33a
EXPECTED_SELECTION_SHA=7a96b17ea0060af080dbe2720833161374344fa0d466b2fe28b0e9402387096e
EXPECTED_FAILED_SUMMARY_SHA=8359facc1299ae6afa58b07804f36d9c5edc6b2725e1b5f7b065527f06e4e83c
EXPECTED_RUNNER_SHA=9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
EXPECTED_DEPENDENCY_SHA=a5ae7fddcfcd16e1715e35cc5a67c3d3d135b2542ff0e132a4cca5817afddf05

test "$(sha256sum "${PREP}" | awk '{print $1}')" = "${EXPECTED_PREP_SHA}"
test "$(sha256sum "${CONTROLLER_SOURCE}" | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA}"
test "$(sha256sum "${SOURCE_STATE}" | awk '{print $1}')" = "${EXPECTED_STATE_SHA}"
test "$(sha256sum "${SOURCE_GATE}" | awk '{print $1}')" = "${EXPECTED_GATE_SHA}"
test "$(sha256sum "${SOURCE_SELECTION}" | awk '{print $1}')" = "${EXPECTED_SELECTION_SHA}"
test "$(sha256sum "${FAILED_SUMMARY}" | awk '{print $1}')" = "${EXPECTED_FAILED_SUMMARY_SHA}"
test "$(sha256sum "${SOURCE}/package/run_job_round8_strict_serial.py" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${SOURCE}/package/job_round8_strict_replay_contract.py" | awk '{print $1}')" = "${EXPECTED_DEPENDENCY_SHA}"
test ! -e "${TARGET}"
test ! -e "${PREP_STDOUT}"
test "$(tr -d '[:space:]' < "${GLOBAL_COUNTER}")" = 321
test "$(tr -d '[:space:]' < "${FORMAL_REPLAY_COUNTER}")" = 181
test "$(tr -d '[:space:]' < "${OLD_ACCEPTED_COUNTER}")" = 2
if tmux has-session -t job_fresh_formal_v33 2>/dev/null; then
    echo "JOB fresh formal tmux session already exists" >&2
    exit 73
fi

python3 -u "${PREP}" \
    --source-state "${SOURCE_STATE}" \
    --source-state-sha256 "${EXPECTED_STATE_SHA}" \
    --source-gate "${SOURCE_GATE}" \
    --source-gate-sha256 "${EXPECTED_GATE_SHA}" \
    --failed-summary "${FAILED_SUMMARY}" \
    --failed-summary-sha256 "${EXPECTED_FAILED_SUMMARY_SHA}" \
    --global-ordinal 321 \
    --formal-replay-count 181 \
    --output-root "${TARGET}" > "${PREP_STDOUT}"

cp "${PREP_STDOUT}" "${PACKAGE}/prepare_stdout.json"
cp "${CONTROLLER_SOURCE}" "${PACKAGE}/job_formal_target10_batch_controller.py"
cp "${SOURCE}/package/run_job_round8_strict_serial.py" "${PACKAGE}/run_job_round8_strict_serial.py"
cp "${SOURCE}/package/job_round8_strict_replay_contract.py" "${PACKAGE}/job_round8_strict_replay_contract.py"
cp "${SOURCE_SELECTION}" "${PACKAGE}/top5_promoted_formal.json"
test "$(sha256sum "${PACKAGE}/job_formal_target10_batch_controller.py" | awk '{print $1}')" = "${EXPECTED_CONTROLLER_SHA}"
test "$(sha256sum "${PACKAGE}/run_job_round8_strict_serial.py" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${PACKAGE}/job_round8_strict_replay_contract.py" | awk '{print $1}')" = "${EXPECTED_DEPENDENCY_SHA}"
test "$(sha256sum "${PACKAGE}/top5_promoted_formal.json" | awk '{print $1}')" = "${EXPECTED_SELECTION_SHA}"

STATE=${PACKAGE}/formal_state.json
GATE=${PACKAGE}/entry_gate.json
SELECTION=${PACKAGE}/top5_promoted_formal.json
STATE_SHA="$(sha256sum "${STATE}" | awk '{print $1}')"
GATE_SHA="$(sha256sum "${GATE}" | awk '{print $1}')"
jq -e '
    .accepted_count == 0 and
    .fresh_formal_campaign.accepted_target == 10 and
    .fresh_formal_campaign.source_accepted_excluded_from_new_total == 2 and
    .global_ordinal == 321 and
    .formal_replay_count == 181
' "${STATE}" >/dev/null
jq -e '
    .passed == true and
    .formal_accepted_total == 0 and
    .accepted_target == 10 and
    .formal_counter_pins_before_start == {
        "formal_search_accepted_count": 0,
        "formal_search_replay_count": 181,
        "global_ordinal": 321
    } and
    ([.checks[]] | all)
' "${GATE}" >/dev/null
test "$(tr -d '[:space:]' < "${NEW_ACCEPTED_COUNTER}")" = 0

python3 -u "${PACKAGE}/run_job_round8_strict_serial.py" \
    "${SELECTION}" "${REPLAY}" \
    --authority-root "${P}/authority" \
    --observations "${P}/job_round8_v6_observations.json" \
    --exclusion "${P}/round7_replayed_fingerprints.json" \
    --state "${STATE}" \
    --accepted-target 10 \
    --dry-run > "${PACKAGE}/dry_run.json"

sha256sum \
    "${PACKAGE}/fresh_campaign_manifest.json" \
    "${STATE}" "${GATE}" "${SELECTION}" \
    "${PACKAGE}/job_formal_target10_batch_controller.py" \
    "${PACKAGE}/run_job_round8_strict_serial.py" \
    "${PACKAGE}/job_round8_strict_replay_contract.py" \
    "${PACKAGE}/dry_run.json" > "${PACKAGE}/sealed.sha256"

tmux new-session -d -s job_fresh_formal_v33 \
    "cd '${PACKAGE}' && exec python3 -u '${PACKAGE}/job_formal_target10_batch_controller.py' \
        --selection '${SELECTION}' \
        --selection-sha256 '${EXPECTED_SELECTION_SHA}' \
        --gate '${GATE}' \
        --gate-sha256 '${GATE_SHA}' \
        --state '${STATE}' \
        --state-sha256 '${STATE_SHA}' \
        --authority-root '${P}/authority' \
        --observations '${P}/job_round8_v6_observations.json' \
        --exclusion '${P}/round7_replayed_fingerprints.json' \
        --replay-root '${REPLAY}' \
        --global-counter '${GLOBAL_COUNTER}' \
        --formal-replay-counter '${FORMAL_REPLAY_COUNTER}' \
        --accepted-counter '${NEW_ACCEPTED_COUNTER}' \
        > '${TARGET}/controller.log' 2>&1"

sleep 2
PID="$(tmux list-panes -t job_fresh_formal_v33 -F '#{pane_pid}')"
test -n "${PID}"
kill -0 "${PID}"
printf '%s\n' "${PID}" > "${TARGET}/controller.pid"
printf 'JOB_FRESH_FORMAL_STARTED pid=%s state_sha=%s gate_sha=%s\n' \
    "${PID}" "${STATE_SHA}" "${GATE_SHA}"
