#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
V52_ROOT=${T}/autonomous_target10_v52r2_fresh0
ROOT=${T}/autonomous_target10_v53_fresh0
STATE=${ROOT}/package/formal_state.json
ACCEPTED=${ROOT}/formal_search_accepted_counter.txt
V49=/tmp/job_fresh10_target10_autonomous_v49.py
V50=/tmp/job_fresh10_target10_autonomous_v50.py
V53=/tmp/job_fresh10_target10_autonomous_v53.py
BOOTSTRAP=/tmp/bootstrap_job_v53_fresh0.py
BASE_V7=/tmp/run_job_round8_strict_serial_base_v7_explicit_preclaim.py
ROBUST_V9=/tmp/run_job_round8_strict_serial_robust_v9_state_guard.py
REMOTE_BRIDGE=${B}/../../../../launch_outer_attempt4_formal_dbqps_v1_retry3/formal_job_outer_replay.py
BRIDGE_SOURCE=/tmp/formal_job_outer_replay_v53.py
BRIDGE_DEPLOY=/tmp/deploy_job_v53_network_bridge.py
JOB_NETWORK=ra_job_v53_net
JOB_BRIDGE_INTERFACE=ra-job-v53

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${V49}" 3688f00b0a32c490f5dd3781b26642f48361c3ba541256f9855730284016035c
require_sha "${V50}" b9dc4ca55d67c4a94938e7498e2359796fae6749d70bb11e704b81124ff43099
require_sha "${V53}" c1ef88ab218f0f318a02c0e7debbfb56362de141e5f07a9e24ed40ab3a2aa544
require_sha "${BOOTSTRAP}" 3b1e33f7b34312176142379feda3182921b958e815b467f00c569882f32efe93
require_sha "${BASE_V7}" 839833efdde6a1294fc43bdd07012562a768039e65c1a146513a1d7911611dd4
require_sha "${ROBUST_V9}" e717449a109669d6ed274e29d5aaf9f4285af8b37c586aeb0ad768970ba02693
require_sha "${BRIDGE_SOURCE}" ed91561652e52a28fc01e6d542577e7b3006b3c836ea5109d162991b00e4aadd
require_sha "${BRIDGE_DEPLOY}" 3e9c541a35cfc2e0c9a0c28480c17e80214efbf533cf8a1df54bd221fb8e48a0

if pgrep -af "${B}.*([r]un_job_round8_strict_serial|[j]ob_formal_target10_batch_controller|[f]ormal_job_outer_replay|[f]ormal_pg_point_runner|[j]ob_fresh10_target10_autonomous)" >/dev/null; then
  echo "active JOB replay/controller detected; refusing v53 overlap" >&2
  exit 73
fi

test -d "${V52_ROOT}"
V52_STATE_SHA=$(sha256sum "${V52_ROOT}/package/formal_state.json" | awk '{print $1}')
test "$(tr -d '[:space:]' < "${V52_ROOT}/formal_search_accepted_counter.txt")" = 0

python3 -u "${BRIDGE_DEPLOY}" > /tmp/job_v53_network_bridge_deploy.json
jq -e '
  .status == "complete" and .workload_calls == 0 and .ordinal_claims == 0 and
  .mysql_touched == false and
  .target_sha256_after == "ed91561652e52a28fc01e6d542577e7b3006b3c836ea5109d162991b00e4aadd"
' /tmp/job_v53_network_bridge_deploy.json >/dev/null
require_sha "${REMOTE_BRIDGE}" ed91561652e52a28fc01e6d542577e7b3006b3c836ea5109d162991b00e4aadd

case "${JOB_NETWORK}" in bridge|host|none) exit 72 ;; esac
if ! docker network inspect "${JOB_NETWORK}" >/dev/null 2>&1; then
  docker network create \
    --driver bridge \
    --opt "com.docker.network.bridge.name=${JOB_BRIDGE_INTERFACE}" \
    --label resource_adaptive.scenario=job \
    --label resource_adaptive.campaign=v53 \
    "${JOB_NETWORK}" > /tmp/job_v53_network_id.txt
fi
docker network inspect "${JOB_NETWORK}" | jq -e --arg name "${JOB_NETWORK}" --arg iface "${JOB_BRIDGE_INTERFACE}" '
  length == 1 and .[0].Name == $name and .[0].Driver == "bridge" and
  .[0].Scope == "local" and .[0].Internal == false and
  (.[0].Containers | length) == 0 and
  .[0].Options["com.docker.network.bridge.name"] == $iface and
  (.[0].IPAM.Config | length) > 0 and
  all(.[0].IPAM.Config[]; (.Subnet | length) > 0 and (.Gateway | length) > 0)
' >/dev/null
export RA_JOB_DOCKER_NETWORK="${JOB_NETWORK}"

if ! test -f "${STATE}"; then
  test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = 397
  test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = 206
  python3 -u "${BOOTSTRAP}" > /tmp/job_v53_fresh0_bootstrap_output.json
  jq -e '
    .status == "complete" and .fresh_accepted_count == 0 and
    .fresh_gradient_observation_count == 0 and
    .fresh_range_is_baseline_point == true and
    .global_ordinal == 397 and .formal_replay_count == 206 and
    .v50_technical_physical_calls == 3 and
    .v52r2_technical_physical_calls == 2 and
    .v52r2_countable_accepted_in_v53 == 0 and
    .old_roots_modified == false and
    .workload_calls == 0 and .explain_calls == 0
  ' /tmp/job_v53_fresh0_bootstrap_output.json >/dev/null
fi

test "$(jq -r '.campaign_contract' "${STATE}")" = job_v53_fresh0_v52r2_technical_excluded_dedicated_network_v1
test "$(jq -r '.accepted_count' "${STATE}")" = "$(tr -d '[:space:]' < "${ACCEPTED}")"
test "$(sha256sum "${V52_ROOT}/package/formal_state.json" | awk '{print $1}')" = "${V52_STATE_SHA}"
STATE_SHA=$(sha256sum "${STATE}" | awk '{print $1}')
GLOBAL_BEFORE=$(tr -d '[:space:]' < "${B}/replay_counter.txt")
FORMAL_BEFORE=$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")
ACCEPTED_BEFORE=$(tr -d '[:space:]' < "${ACCEPTED}")

PREFLIGHT=${ROOT}/preflights/v53_preflight_${STATE_SHA}_${GLOBAL_BEFORE}.json
mkdir -p "$(dirname "${PREFLIGHT}")"
if ! test -f "${PREFLIGHT}"; then
  TEMP=${PREFLIGHT}.tmp.$$
  python3 -u "${V53}" --preflight-only > "${TEMP}"
  mv "${TEMP}" "${PREFLIGHT}"
fi
jq -e '
  .status == "passed" and .controller == "job_v53" and
  .new_workload_calls == 0 and .explain_calls == 0
' "${PREFLIGHT}" >/dev/null
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${STATE_SHA}"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = "${GLOBAL_BEFORE}"
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = "${FORMAL_BEFORE}"
test "$(tr -d '[:space:]' < "${ACCEPTED}")" = "${ACCEPTED_BEFORE}"
test "$(sha256sum "${V52_ROOT}/package/formal_state.json" | awk '{print $1}')" = "${V52_STATE_SHA}"
require_sha "${REMOTE_BRIDGE}" ed91561652e52a28fc01e6d542577e7b3006b3c836ea5109d162991b00e4aadd
docker network inspect "${JOB_NETWORK}" | jq -e '(.[0].Containers | length) == 0' >/dev/null

exec python3 -u "${V53}"
