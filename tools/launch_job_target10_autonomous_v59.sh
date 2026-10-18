#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
ROOT=${T}/autonomous_target10_v57_fresh0
STATE=${ROOT}/package/formal_state.json
ACCEPTED=${ROOT}/formal_search_accepted_counter.txt
V59=/tmp/job_fresh10_target10_autonomous_v59.py
V58=/tmp/job_fresh10_target10_autonomous_v58.py
ROBUST_V11=/tmp/run_job_round8_strict_serial_robust_v11_gradient_state_guard.py
FORMAL_V2=/tmp/job_formal_target10_batch_controller_v2_gradient_commit.py
JOB_NETWORK=ra_job_v53_net
JOB_BRIDGE_INTERFACE=ra-job-v53

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${V59}" 994c27feba747152372e0155f58d674f47ee306de68fc4ed7f9f4750f51b56d1
require_sha "${V58}" 8ed24530017d866c1c9ca3ce31e5d4e69a1c7083b6237827c274c96093623a30
require_sha "${ROBUST_V11}" d8919ef67c0b9fe0b9666717e54eedb8cf197b835d4a1e1a918c18674e1b3043
require_sha "${FORMAL_V2}" e5e4a090b3731add51005c00996cb2af7c450a3c491708321cc96d5d74cf294b

if pgrep -af "${B}.*([r]un_job_round8_strict_serial|[j]ob_formal_target10_batch_controller|[f]ormal_job_outer_replay|[f]ormal_pg_point_runner|[j]ob_fresh10_target10_autonomous)" >/dev/null; then
  echo "active JOB replay/controller detected; refusing v59 overlap" >&2
  exit 73
fi

case "${JOB_NETWORK}" in
  bridge|host|none) echo "prohibited JOB network" >&2; exit 72 ;;
esac
test -f "${STATE}"
test -f "${ACCEPTED}"
STATE_SHA=$(sha256sum "${STATE}" | awk '{print $1}')
GLOBAL_BEFORE=$(tr -d '[:space:]' < "${B}/replay_counter.txt")
FORMAL_BEFORE=$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")
ACCEPTED_BEFORE=$(tr -d '[:space:]' < "${ACCEPTED}")
test "$(jq -r '.accepted_count' "${STATE}")" = "${ACCEPTED_BEFORE}"

PREFLIGHT_DIR=${ROOT}/preflights
mkdir -p "${PREFLIGHT_DIR}"
NETWORK_RAW=${PREFLIGHT_DIR}/v59_network_raw_${STATE_SHA}_${GLOBAL_BEFORE}.json
NETWORK_AUDIT=${PREFLIGHT_DIR}/v59_network_contract_${STATE_SHA}_${GLOBAL_BEFORE}.json
if ! test -f "${NETWORK_RAW}"; then
  TEMP=${NETWORK_RAW}.tmp.$$
  docker network inspect "${JOB_NETWORK}" > "${TEMP}"
  mv "${TEMP}" "${NETWORK_RAW}"
fi
jq -e --arg name "${JOB_NETWORK}" --arg iface "${JOB_BRIDGE_INTERFACE}" '
  length == 1 and .[0].Name == $name and .[0].Driver == "bridge" and
  .[0].Scope == "local" and .[0].Internal == false and
  (.[0].Containers | length) == 0 and
  .[0].Options["com.docker.network.bridge.name"] == $iface
' "${NETWORK_RAW}" >/dev/null
NETWORK_RAW_SHA=$(sha256sum "${NETWORK_RAW}" | awk '{print $1}')
if ! test -f "${NETWORK_AUDIT}"; then
  TEMP=${NETWORK_AUDIT}.tmp.$$
  jq -n \
    --arg contract job_v59_dedicated_bridge_preflight_v1 \
    --arg network_name "${JOB_NETWORK}" \
    --arg bridge_interface "${JOB_BRIDGE_INTERFACE}" \
    --arg network_inspect_path "${NETWORK_RAW}" \
    --arg network_inspect_sha256 "${NETWORK_RAW_SHA}" \
    --arg formal_state_sha256 "${STATE_SHA}" \
    --argjson accepted_count "${ACCEPTED_BEFORE}" \
    --argjson global_counter "${GLOBAL_BEFORE}" \
    --argjson formal_replay_counter "${FORMAL_BEFORE}" \
    --argjson accepted_counter "${ACCEPTED_BEFORE}" \
    '{
      contract: $contract,
      network_name: $network_name,
      driver: "bridge",
      scope: "local",
      internal: false,
      bridge_interface: $bridge_interface,
      attached_container_count: 0,
      network_inspect_path: $network_inspect_path,
      network_inspect_sha256: $network_inspect_sha256,
      formal_state_sha256: $formal_state_sha256,
      accepted_count: $accepted_count,
      global_counter: $global_counter,
      formal_replay_counter: $formal_replay_counter,
      accepted_counter: $accepted_counter,
      mysql_touched: false,
      new_workload_calls: 0
    }' > "${TEMP}"
  mv "${TEMP}" "${NETWORK_AUDIT}"
fi
NETWORK_AUDIT_SHA=$(sha256sum "${NETWORK_AUDIT}" | awk '{print $1}')

test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${STATE_SHA}"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = "${GLOBAL_BEFORE}"
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = "${FORMAL_BEFORE}"
test "$(tr -d '[:space:]' < "${ACCEPTED}")" = "${ACCEPTED_BEFORE}"

export RA_JOB_DOCKER_NETWORK="${JOB_NETWORK}"
export RA_JOB_NETWORK_PREFLIGHT_JSON="${NETWORK_AUDIT}"
export RA_JOB_NETWORK_PREFLIGHT_SHA256="${NETWORK_AUDIT_SHA}"

PREFLIGHT=${PREFLIGHT_DIR}/v59_preflight_${STATE_SHA}_${GLOBAL_BEFORE}.json
if ! test -f "${PREFLIGHT}"; then
  TEMP=${PREFLIGHT}.tmp.$$
  python3 -u "${V59}" --preflight-only > "${TEMP}"
  mv "${TEMP}" "${PREFLIGHT}"
fi
jq -e --argjson accepted "${ACCEPTED_BEFORE}" '
  .status == "passed" and .controller == "job_v59" and
  .accepted_count == $accepted and .new_workload_calls == 0 and .explain_calls == 0
' "${PREFLIGHT}" >/dev/null
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${STATE_SHA}"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = "${GLOBAL_BEFORE}"
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = "${FORMAL_BEFORE}"
test "$(tr -d '[:space:]' < "${ACCEPTED}")" = "${ACCEPTED_BEFORE}"
docker network inspect "${JOB_NETWORK}" | jq -e '(.[0].Containers | length) == 0' >/dev/null

if [[ "${V59_PREFLIGHT_ONLY:-0}" = "1" ]]; then
  jq . "${PREFLIGHT}"
  exit 0
fi

exec python3 -u "${V59}"
