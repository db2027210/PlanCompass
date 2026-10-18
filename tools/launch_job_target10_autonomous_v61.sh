#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
ROOT=${T}/autonomous_target10_v57_fresh0
STATE=${ROOT}/package/formal_state.json
ACCEPTED=${ROOT}/formal_search_accepted_counter.txt
FRESH=${ROOT}/cycle_002/fresh_discovery_05
V61=/tmp/job_fresh10_target10_autonomous_v61.py
V60=/tmp/job_fresh10_target10_autonomous_v60.py
JOB_NETWORK=ra_job_v53_net
JOB_BRIDGE_INTERFACE=ra-job-v53

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${V61}" 49e6c210380c0852d9b0dffd7da597d318de1932994351c31b028cc6cbf53053
require_sha "${V60}" 394f207eea62d93ceae570d3bca25abe36c4ae787a37f8913cbbc4e492ed222b

if pgrep -af "${B}.*([r]un_job_round8_strict_serial|[j]ob_formal_target10_batch_controller|[f]ormal_job_outer_replay|[f]ormal_pg_point_runner|[j]ob_fresh10_target10_autonomous)" >/dev/null; then
  echo "active JOB replay/controller detected; refusing v61 overlap" >&2
  exit 73
fi

test -f "${STATE}"
test -f "${ACCEPTED}"
STATE_SHA=$(sha256sum "${STATE}" | awk '{print $1}')
GLOBAL_BEFORE=$(tr -d '[:space:]' < "${B}/replay_counter.txt")
FORMAL_BEFORE=$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")
ACCEPTED_BEFORE=$(tr -d '[:space:]' < "${ACCEPTED}")
test "${STATE_SHA}" = 8ee73ee31883f0362353ec0e03dc665ef113f91510269a07b6f28109cfda62a7
test "${GLOBAL_BEFORE}" = 424
test "${FORMAL_BEFORE}" = 211
test "${ACCEPTED_BEFORE}" = 1
test "$(jq -r '.accepted_count' "${STATE}")" = "${ACCEPTED_BEFORE}"

NETWORK_AUDIT=${ROOT}/preflights/v60_network_contract_${STATE_SHA}_${GLOBAL_BEFORE}.json
test -f "${NETWORK_AUDIT}"
NETWORK_RAW=$(jq -r '.network_inspect_path' "${NETWORK_AUDIT}")
NETWORK_RAW_SHA=$(jq -r '.network_inspect_sha256' "${NETWORK_AUDIT}")
require_sha "${NETWORK_RAW}" "${NETWORK_RAW_SHA}"
jq -e --arg name "${JOB_NETWORK}" --arg iface "${JOB_BRIDGE_INTERFACE}" '
  .contract == "job_v59_dedicated_bridge_preflight_v1" and
  .network_name == $name and .driver == "bridge" and .scope == "local" and
  .internal == false and .bridge_interface == $iface and
  .attached_container_count == 0 and
  .formal_state_sha256 == "8ee73ee31883f0362353ec0e03dc665ef113f91510269a07b6f28109cfda62a7" and
  .accepted_count == 1 and .global_counter == 424 and
  .formal_replay_counter == 211 and .accepted_counter == 1
' "${NETWORK_AUDIT}" >/dev/null
docker network inspect "${JOB_NETWORK}" | jq -e --arg name "${JOB_NETWORK}" --arg iface "${JOB_BRIDGE_INTERFACE}" '
  length == 1 and .[0].Name == $name and .[0].Driver == "bridge" and
  .[0].Scope == "local" and .[0].Internal == false and
  (.[0].Containers | length) == 0 and
  .[0].Options["com.docker.network.bridge.name"] == $iface
' >/dev/null

RECOVERY_AUDIT=${FRESH}/v61_partial_recovery_audit.json
if ! test -f "${RECOVERY_AUDIT}"; then
  require_sha "${FRESH}/exclusion.json" b3c9bfa581912b68ee574b312e224aec7ee60d5277932dea0390cf778e5c1dfb
  require_sha "${FRESH}/v55_successor_budget.json" 7227bb14d41ad29e0dea76c031b20f69c34578988217998513fa70714b3cea22
  require_sha "${FRESH}/historical_training_a.json.progress.json" 8ea0b00e3b0e4b84764ec75e242cc28fdba6f5cf7305d63f877983ad6787da4b
  test ! -e "${FRESH}/top5.json"
  test ! -e "${FRESH}/top5.second.json"
fi

export RA_JOB_DOCKER_NETWORK="${JOB_NETWORK}"
export RA_JOB_NETWORK_PREFLIGHT_JSON="${NETWORK_AUDIT}"
export RA_JOB_NETWORK_PREFLIGHT_SHA256="$(sha256sum "${NETWORK_AUDIT}" | awk '{print $1}')"

PREFLIGHT=${ROOT}/preflights/v61_preflight_${STATE_SHA}_${GLOBAL_BEFORE}.json
if ! test -f "${PREFLIGHT}"; then
  TEMP=${PREFLIGHT}.tmp.$$
  python3 -u "${V61}" --preflight-only > "${TEMP}"
  mv "${TEMP}" "${PREFLIGHT}"
fi
jq -e '
  .status == "passed" and .controller == "job_v61" and
  .accepted_count == 1 and .new_workload_calls == 0 and .explain_calls == 0
' "${PREFLIGHT}" >/dev/null
jq -e '
  .status == "committed" and
  .contract == "job_v61_restart_safe_successor_partial_recovery_v1" and
  .recovery_epoch == "job_v61_cycle002_attempt05_generator_recovery_v1" and
  .cursor_reusable == false and .replacement_generator_invocation_limit == 2 and
  .new_workload_calls == 0 and .formal_state_updates == 0 and .counter_updates == 0 and
  .old_partial_artifacts_overwritten == false
' "${RECOVERY_AUDIT}" >/dev/null
require_sha "${FRESH}/partial_quarantine_v61/v55_successor_budget.json" 7227bb14d41ad29e0dea76c031b20f69c34578988217998513fa70714b3cea22
require_sha "${FRESH}/partial_quarantine_v61/historical_training_a.json.progress.json" 8ea0b00e3b0e4b84764ec75e242cc28fdba6f5cf7305d63f877983ad6787da4b
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${STATE_SHA}"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = "${GLOBAL_BEFORE}"
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = "${FORMAL_BEFORE}"
test "$(tr -d '[:space:]' < "${ACCEPTED}")" = "${ACCEPTED_BEFORE}"
docker network inspect "${JOB_NETWORK}" | jq -e '(.[0].Containers | length) == 0' >/dev/null

if [[ "${V61_PREFLIGHT_ONLY:-0}" = "1" ]]; then
  jq . "${PREFLIGHT}"
  exit 0
fi

exec python3 -u "${V61}"
