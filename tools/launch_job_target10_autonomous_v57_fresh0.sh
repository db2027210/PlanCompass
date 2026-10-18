#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
V55_ROOT=${T}/autonomous_target10_v55_fresh0
ROOT=${T}/autonomous_target10_v57_fresh0
STATE=${ROOT}/package/formal_state.json
ACCEPTED=${ROOT}/formal_search_accepted_counter.txt
V49=/tmp/job_fresh10_target10_autonomous_v49.py
V50=/tmp/job_fresh10_target10_autonomous_v50.py
V55=/tmp/job_fresh10_target10_autonomous_v55.py
V57=/tmp/job_fresh10_target10_autonomous_v57.py
BOOTSTRAP=/tmp/bootstrap_job_v57_fresh0.py
BASE_V10=/tmp/run_job_round8_strict_serial_base_v10_sealed_explain.py
BASE_V11=/tmp/run_job_round8_strict_serial_base_v11_explicit_selection.py
ROBUST_V8=/tmp/run_job_round8_strict_serial_robust_v8_apply_failure.py
ROBUST_V9=/tmp/run_job_round8_strict_serial_robust_v9_state_guard.py
ROBUST_V10=/tmp/run_job_round8_strict_serial_robust_v10_resource_median.py
REMOTE_BRIDGE=${B}/../../../../launch_outer_attempt4_formal_dbqps_v1_retry3/formal_job_outer_replay.py
JOB_NETWORK=ra_job_v53_net
JOB_BRIDGE_INTERFACE=ra-job-v53

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${V49}" 3688f00b0a32c490f5dd3781b26642f48361c3ba541256f9855730284016035c
require_sha "${V50}" 2cf4ecd0e1816c6e46013ef2c093b7b3caf7ca5d945cd107ab0bd97696da9804
require_sha "${V55}" cc9b19977c140a2d42d0c33abf11250e607afc65fbff0f89eb5d3d6c8a357dd0
require_sha "${V57}" ebc884eefe3cc5663ec4ae5738d46ff564afe78e4b56c68ef4b440996198285b
require_sha "${BOOTSTRAP}" ed9b5964a49b4e70f8f615f6928a67e5f747e83f989772e72b159442f8e45002
require_sha "${BASE_V10}" 6640148153dceee614f04a96096883a1ef8ce12b6eaa84a761c59593640985d7
require_sha "${BASE_V11}" 13ad9a6a78c6438794c9c36688b91e0398f8ae5a8eae115c56e4161adc8c23ab
require_sha "${ROBUST_V8}" 5d965cd9410fba78486856dc2d78bd594b9ebf1d17eda4a8f593a5f43d6fa857
require_sha "${ROBUST_V9}" e717449a109669d6ed274e29d5aaf9f4285af8b37c586aeb0ad768970ba02693
require_sha "${ROBUST_V10}" e4738c9eb95e123a38e31410bb067bf817bb8b54e234acbb2f5bc14ed89f2b03
require_sha "${REMOTE_BRIDGE}" 406e56aea7b148840a64dff88b190468ed78f6f3683468553cbe31ed57baef23

if pgrep -af "${B}.*([r]un_job_round8_strict_serial|[j]ob_formal_target10_batch_controller|[f]ormal_job_outer_replay|[f]ormal_pg_point_runner|[j]ob_fresh10_target10_autonomous)" >/dev/null; then
  echo "active JOB replay/controller detected; refusing v57 overlap" >&2
  exit 73
fi

test -f "${V55_ROOT}/package/formal_state.json"
V55_STATE_SHA=$(sha256sum "${V55_ROOT}/package/formal_state.json" | awk '{print $1}')
test "$(jq -r '.accepted_count' "${V55_ROOT}/package/formal_state.json")" = 0
test "$(tr -d '[:space:]' < "${V55_ROOT}/formal_search_accepted_counter.txt")" = 0

case "${JOB_NETWORK}" in bridge|host|none) exit 72 ;; esac
docker network inspect "${JOB_NETWORK}" | jq -e --arg name "${JOB_NETWORK}" --arg iface "${JOB_BRIDGE_INTERFACE}" '
  length == 1 and .[0].Name == $name and .[0].Driver == "bridge" and
  .[0].Scope == "local" and .[0].Internal == false and
  (.[0].Containers | length) == 0 and
  .[0].Options["com.docker.network.bridge.name"] == $iface
' >/dev/null
export RA_JOB_DOCKER_NETWORK="${JOB_NETWORK}"

if ! test -f "${STATE}"; then
  python3 -u "${BOOTSTRAP}" > /tmp/job_v57_fresh0_bootstrap_output.json
  jq -e '
    .status == "complete" and .fresh_accepted_count == 0 and
    .fresh_gradient_observation_count == 0 and
    .fresh_range_is_baseline_point == true and
    .v55_countable_accepted_in_v57 == 0 and
    .v55_diagnostics_reused_as_training_only == true and
    .v55_failed_formal_quarantined_no_retry == true and
    .old_roots_modified == false and .workload_calls == 0 and .explain_calls == 0
  ' /tmp/job_v57_fresh0_bootstrap_output.json >/dev/null
fi

test "$(jq -r '.campaign_contract' "${STATE}")" = job_v57_fresh0_resource_median_no_retry_v1
test "$(jq -r '.accepted_count' "${STATE}")" = 0
test "$(tr -d '[:space:]' < "${ACCEPTED}")" = 0
test "$(jq -r '.historical_v55_failed_formal_retry_allowed' "${STATE}")" = false
test "$(sha256sum "${V55_ROOT}/package/formal_state.json" | awk '{print $1}')" = "${V55_STATE_SHA}"

STATE_SHA=$(sha256sum "${STATE}" | awk '{print $1}')
GLOBAL_BEFORE=$(tr -d '[:space:]' < "${B}/replay_counter.txt")
FORMAL_BEFORE=$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")
PREFLIGHT=${ROOT}/preflights/v57_preflight_${STATE_SHA}_${GLOBAL_BEFORE}.json
mkdir -p "$(dirname "${PREFLIGHT}")"
if ! test -f "${PREFLIGHT}"; then
  TEMP=${PREFLIGHT}.tmp.$$
  python3 -u "${V57}" --preflight-only > "${TEMP}"
  mv "${TEMP}" "${PREFLIGHT}"
fi
jq -e '
  .status == "passed" and .controller == "job_v57" and
  .accepted_count == 0 and .new_workload_calls == 0 and .explain_calls == 0
' "${PREFLIGHT}" >/dev/null
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${STATE_SHA}"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = "${GLOBAL_BEFORE}"
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = "${FORMAL_BEFORE}"
test "$(tr -d '[:space:]' < "${ACCEPTED}")" = 0
test "$(sha256sum "${V55_ROOT}/package/formal_state.json" | awk '{print $1}')" = "${V55_STATE_SHA}"
require_sha "${REMOTE_BRIDGE}" 406e56aea7b148840a64dff88b190468ed78f6f3683468553cbe31ed57baef23
docker network inspect "${JOB_NETWORK}" | jq -e '(.[0].Containers | length) == 0' >/dev/null

exec python3 -u "${V57}"
