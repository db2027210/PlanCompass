#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
ROOT=${T}/autonomous_target10_v57_fresh0
STATE=${ROOT}/package/formal_state.json
CONTROLLER_STATE=${ROOT}/controller_state.json
ACCEPTED=${ROOT}/formal_search_accepted_counter.txt
CYCLE=${ROOT}/cycle_002
REPEAT=${CYCLE}/repeat_01
V63=/tmp/job_fresh10_target10_autonomous_v63.py
V62=/tmp/job_fresh10_target10_autonomous_v62.py
V61=/tmp/job_fresh10_target10_autonomous_v61.py
V60=/tmp/job_fresh10_target10_autonomous_v60.py

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${V63}" 15b7e1658d3328a3a92d2648ed610cf9153ec2e26e848b46fc2e756a30a74980
require_sha "${V62}" 748ba44a28c446a8510ca1463750b1db3c7a9674f46cd9d4d0876c3c2c021a2a
require_sha "${V61}" 49e6c210380c0852d9b0dffd7da597d318de1932994351c31b028cc6cbf53053
require_sha "${V60}" 394f207eea62d93ceae570d3bca25abe36c4ae787a37f8913cbbc4e492ed222b

if pgrep -af "${B}.*([r]un_job_round8_strict_serial|[j]ob_formal_target10_batch_controller|[f]ormal_job_outer_replay|[f]ormal_pg_point_runner|[j]ob_fresh10_target10_autonomous)" >/dev/null; then
  echo "active JOB replay/controller detected; refusing v63 overlap" >&2
  exit 73
fi

require_sha "${STATE}" 8ee73ee31883f0362353ec0e03dc665ef113f91510269a07b6f28109cfda62a7
require_sha "${CYCLE}/v62_v61_clean5_authority_bridge.json" b61f0102cde225af27bd9b9fc308282673f0c35e88441979b2de2a93fac1e637
require_sha "${REPEAT}/top5.json" 9ef43934cb9d01047690f1ccc0414f5eb8a1f09a3807c264712450da9d4b15a1
require_sha "${REPEAT}/top5.second.json" 9ef43934cb9d01047690f1ccc0414f5eb8a1f09a3807c264712450da9d4b15a1
test -f "${REPEAT}/counters_before.json"
test -f "${REPEAT}/v50_batch_sequence.json"
test ! -e "${REPEAT}/replay"
test ! -e "${REPEAT}/diagnostic_audit.json"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = 429
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = 211
test "$(tr -d '[:space:]' < "${ACCEPTED}")" = 1
jq -e '.global == 429 and .formal_replay == 211 and .accepted == 1' \
  "${REPEAT}/counters_before.json" >/dev/null

CONTROLLER_SHA=$(sha256sum "${CONTROLLER_STATE}" | awk '{print $1}')
if test "${CONTROLLER_SHA}" = 819845b7689665bb4d7e4d73ea2f149a590667fad0b4beff4323b527260bb841; then
  jq -e --arg phase "${REPEAT}" '
    .schema_version == "job_fresh10_target10_autonomous_v62" and
    .status == "fail_closed" and
    .error == ("partial diagnostic has no sealed summary; refusing replay: " + $phase)
  ' "${CONTROLLER_STATE}" >/dev/null
else
  jq -e '
    .schema_version == "job_fresh10_target10_autonomous_v63" and
    .status == "ready_resume" and .cycle == 2 and .phase == "repeat_gate" and
    .accepted_count == 1 and .new_workload_calls == 0 and
    .formal_state_updates == 0 and .counter_updates == 0
  ' "${CONTROLLER_STATE}" >/dev/null
fi

mkdir -p "${ROOT}/preflights"
PREFLIGHT=${ROOT}/preflights/v63_preflight_$(date +%Y%m%dT%H%M%S)_$$.json
TEMP=${PREFLIGHT}.tmp
python3 -u "${V63}" --preflight-only > "${TEMP}"
mv "${TEMP}" "${PREFLIGHT}"
jq -e '
  .status == "passed" and .controller == "job_v63" and
  .accepted_count == 1 and .first_cycle == 2 and
  .new_workload_calls == 0 and .explain_calls == 0
' "${PREFLIGHT}" >/dev/null

AUTHORIZATION=${REPEAT}/v63_preworkload_resume_authorization.json
require_sha "${STATE}" 8ee73ee31883f0362353ec0e03dc665ef113f91510269a07b6f28109cfda62a7
require_sha "${REPEAT}/top5.json" 9ef43934cb9d01047690f1ccc0414f5eb8a1f09a3807c264712450da9d4b15a1
jq -e '
  .status == "sealed_zero_physical_ready_for_one_runner_boundary" and
  .physical_calls_before_resume == 0 and
  .same_selection_reuse_only == true and
  .runner_launch_permitted_once == true and
  .new_workload_calls == 0 and .formal_state_updates == 0 and
  .counter_updates == 0 and .selection_rebuilt == false and
  .selection_quarantined == false and .discovery_repeated == false
' "${AUTHORIZATION}" >/dev/null
test ! -e "${REPEAT}/replay"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = 429
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = 211
test "$(tr -d '[:space:]' < "${ACCEPTED}")" = 1

if [[ "${V63_PREFLIGHT_ONLY:-0}" = "1" ]]; then
  jq . "${PREFLIGHT}"
  exit 0
fi

exec python3 -u "${V63}"
