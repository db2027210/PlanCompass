#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
ROOT=${T}/autonomous_target10_v57_fresh0
STATE=${ROOT}/package/formal_state.json
CONTROLLER_STATE=${ROOT}/controller_state.json
ACCEPTED=${ROOT}/formal_search_accepted_counter.txt
CYCLE=${ROOT}/cycle_002
V62=/tmp/job_fresh10_target10_autonomous_v62.py
V61=/tmp/job_fresh10_target10_autonomous_v61.py
V60=/tmp/job_fresh10_target10_autonomous_v60.py

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${V62}" 748ba44a28c446a8510ca1463750b1db3c7a9674f46cd9d4d0876c3c2c021a2a
require_sha "${V61}" 49e6c210380c0852d9b0dffd7da597d318de1932994351c31b028cc6cbf53053
require_sha "${V60}" 394f207eea62d93ceae570d3bca25abe36c4ae787a37f8913cbbc4e492ed222b

if pgrep -af "${B}.*([r]un_job_round8_strict_serial|[j]ob_formal_target10_batch_controller|[f]ormal_job_outer_replay|[f]ormal_pg_point_runner|[j]ob_fresh10_target10_autonomous)" >/dev/null; then
  echo "active JOB replay/controller detected; refusing v62 overlap" >&2
  exit 73
fi

test -f "${STATE}"
test -f "${CONTROLLER_STATE}"
test -f "${ACCEPTED}"
test -f "${CYCLE}/discovery_replay_05/replay/summary.json"
test -f "${CYCLE}/discovery_replay_05/diagnostic_audit.json"
test -f "${CYCLE}/repeat_01/top5.json"
STATE_SHA=$(sha256sum "${STATE}" | awk '{print $1}')
GLOBAL_BEFORE=$(tr -d '[:space:]' < "${B}/replay_counter.txt")
FORMAL_BEFORE=$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")
ACCEPTED_BEFORE=$(tr -d '[:space:]' < "${ACCEPTED}")
test "${STATE_SHA}" = 8ee73ee31883f0362353ec0e03dc665ef113f91510269a07b6f28109cfda62a7
test "${GLOBAL_BEFORE}" = 429
test "${FORMAL_BEFORE}" = 211
test "${ACCEPTED_BEFORE}" = 1
test "$(jq -r '.accepted_count' "${STATE}")" = "${ACCEPTED_BEFORE}"

jq -e '
  .physical_invocation_count == 5 and
  .successful_real_replay_count == 5 and
  .technical_failure_count == 0 and
  .diagnostic_only == true and
  .baseline_params_restored == true and
  .initial_state == .final_state
' "${CYCLE}/discovery_replay_05/replay/summary.json" >/dev/null
jq -e '
  .status == "clean5_state_neutral" and .physical_calls == 5 and
  .valid_replays == 5 and .technical_failures == 0 and .accepted_added == 0
' "${CYCLE}/discovery_replay_05/diagnostic_audit.json" >/dev/null

CONTROLLER_STATUS=$(jq -r '.status' "${CONTROLLER_STATE}")
case "${CONTROLLER_STATUS}" in
  fail_closed|ready_resume) ;;
  *) echo "unexpected JOB controller state for v62: ${CONTROLLER_STATUS}" >&2; exit 74 ;;
esac

PREFLIGHT=${ROOT}/preflights/v62_preflight_${STATE_SHA}_${GLOBAL_BEFORE}.json
if ! test -f "${PREFLIGHT}"; then
  TEMP=${PREFLIGHT}.tmp.$$
  python3 -u "${V62}" --preflight-only > "${TEMP}"
  mv "${TEMP}" "${PREFLIGHT}"
fi
jq -e '
  .status == "passed" and .controller == "job_v62" and
  .accepted_count == 1 and .first_cycle == 2 and
  .new_workload_calls == 0 and .explain_calls == 0
' "${PREFLIGHT}" >/dev/null

BRIDGE=${CYCLE}/v62_v61_clean5_authority_bridge.json
test -f "${BRIDGE}"
jq -e '
  .status == "sealed_state_neutral_transition" and
  .contract == "job_v62_v61_clean5_authority_bridge_v1" and
  .authority_before.counters.global == 424 and
  .authority_after.counters.global == 429 and
  .authority_after.counters.formal_replay == 211 and
  .authority_after.counters.accepted == 1 and
  .physical_transition == 5 and .valid_replays == 5 and
  .technical_failures == 0 and .accepted_delta == 0 and
  .reuse_repeat_01 == true and .new_workload_calls == 0
' "${BRIDGE}" >/dev/null
jq -e --arg bridge_sha "$(sha256sum "${BRIDGE}" | awk '{print $1}')" '
  .schema_version == "job_fresh10_target10_autonomous_v62" and
  .status == "ready_resume" and .cycle == 2 and .phase == "repeat_gate" and
  .accepted_count == 1 and .authority_bridge_sha256 == $bridge_sha and
  .new_workload_calls == 0 and .formal_state_updates == 0 and .counter_updates == 0
' "${CONTROLLER_STATE}" >/dev/null

test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${STATE_SHA}"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = "${GLOBAL_BEFORE}"
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = "${FORMAL_BEFORE}"
test "$(tr -d '[:space:]' < "${ACCEPTED}")" = "${ACCEPTED_BEFORE}"

if [[ "${V62_PREFLIGHT_ONLY:-0}" = "1" ]]; then
  jq . "${PREFLIGHT}"
  exit 0
fi

exec python3 -u "${V62}"
