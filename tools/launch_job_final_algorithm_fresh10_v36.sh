#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
P=${B}/job_round8_corrected_top5_v6_20260731
TARGET=${B}/job_final_algorithm_fresh10_v36_20260731T2020
PACKAGE=${TARGET}/package
REPLAY=${TARGET}/batch001
STATE=${PACKAGE}/formal_state.json
GATE=${PACKAGE}/entry_gate.json
SELECTION=${PACKAGE}/top5_promoted_formal.json
GLOBAL_COUNTER=${B}/replay_counter.txt
FORMAL_REPLAY_COUNTER=${B}/formal_search_replay_counter.txt
ACCEPTED_COUNTER=${PACKAGE}/formal_search_accepted_counter.txt
SESSION=job_final_fresh10_v36

STATE_SHA=57a5ceef1e62770d316b0baafc146542ab5ab47b5e774d160928aa0b49e4f0a7
GATE_SHA=a8f7a6e540b8c727003d949a7304fced0f8d307197c3a45e72a13aba5be0e873
SELECTION_SHA=b80aefae16815373a5648007eda9ea35b648da5f75e0abf2050ce032f5fe1f6d
CONTROLLER_SHA=df512fdecaa2d98f23144991a75ed4eb68c32ec5cc143ff9d9bfb1232f179356
RUNNER_SHA=9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
CONTRACT_SHA=a5ae7fddcfcd16e1715e35cc5a67c3d3d135b2542ff0e132a4cca5817afddf05

test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${STATE_SHA}"
test "$(sha256sum "${GATE}" | awk '{print $1}')" = "${GATE_SHA}"
test "$(sha256sum "${SELECTION}" | awk '{print $1}')" = "${SELECTION_SHA}"
test "$(sha256sum "${PACKAGE}/job_formal_target10_batch_controller.py" | awk '{print $1}')" = "${CONTROLLER_SHA}"
test "$(sha256sum "${PACKAGE}/run_job_round8_strict_serial.py" | awk '{print $1}')" = "${RUNNER_SHA}"
test "$(sha256sum "${PACKAGE}/job_round8_strict_replay_contract.py" | awk '{print $1}')" = "${CONTRACT_SHA}"
test ! -e "${REPLAY}"
test "$(tr -d '[:space:]' < "${GLOBAL_COUNTER}")" = 331
test "$(tr -d '[:space:]' < "${FORMAL_REPLAY_COUNTER}")" = 186
test "$(tr -d '[:space:]' < "${ACCEPTED_COUNTER}")" = 0
if tmux has-session -t "${SESSION}" 2>/dev/null; then
    echo "JOB v36 tmux session already exists" >&2
    exit 73
fi

jq -e '
  .accepted_count == 0 and .accepted_target == 10 and
  .incumbent_score == 4.416709205648302 and
  .incumbent_params.cpu_tuple_cost == 0.01 and
  ([.range_by_parameter[] | .min == .max] | all) and
  .global_ordinal == 331 and .formal_replay_count == 186
' "${STATE}" >/dev/null
jq -e '
  .passed == true and ([.checks[]] | all) and
  .formal_accepted_total == 0 and .accepted_target == 10 and
  .formal_counter_pins_before_start == {
    "formal_search_accepted_count": 0,
    "formal_search_replay_count": 186,
    "global_ordinal": 331
  }
' "${GATE}" >/dev/null
jq -e '
  .diagnostic_only == false and .diagnostic_role_evaluation_only == false and
  [.top5[].selection_role] == ["trusted","trusted","trusted","probe","probe"] and
  ([.top5[:3][] | .incumbent_update_eligible == true and .used_for_gradient == true] | all) and
  ([.top5[3:][] | .incumbent_update_eligible == false and .used_for_gradient == false] | all) and
  ([.top5[].initial_params.cpu_tuple_cost == 0.01] | all)
' "${SELECTION}" >/dev/null

python3 -u "${PACKAGE}/run_job_round8_strict_serial.py" \
  "${SELECTION}" "${REPLAY}" \
  --authority-root "${P}/authority" \
  --observations "${P}/job_round8_v6_observations.json" \
  --exclusion "${P}/round7_replayed_fingerprints.json" \
  --state "${STATE}" --accepted-target 10 --dry-run \
  > "${PACKAGE}/dry_run.json"

tmux new-session -d -s "${SESSION}" \
  "exec bash '${PACKAGE}/run_controller.sh' > '${TARGET}/controller.log' 2>&1"

sleep 2
PID="$(tmux list-panes -t "${SESSION}" -F '#{pane_pid}')"
test -n "${PID}"
kill -0 "${PID}"
printf '%s\n' "${PID}" > "${TARGET}/controller.pid"
printf 'JOB_FRESH_V36_STARTED pid=%s\n' "${PID}"
