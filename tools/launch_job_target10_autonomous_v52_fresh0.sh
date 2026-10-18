#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
V50_ROOT=${T}/autonomous_target10_v50_fresh0
ROOT=${T}/autonomous_target10_v52r2_fresh0
STATE=${ROOT}/package/formal_state.json
ACCEPTED=${ROOT}/formal_search_accepted_counter.txt
V49=/tmp/job_fresh10_target10_autonomous_v49.py
V50=/tmp/job_fresh10_target10_autonomous_v50.py
V52=/tmp/job_fresh10_target10_autonomous_v52.py
BOOTSTRAP=/tmp/bootstrap_job_v52_fresh0.py
BASE_V7=/tmp/run_job_round8_strict_serial_base_v7_explicit_preclaim.py
ROBUST_V9=/tmp/run_job_round8_strict_serial_robust_v9_state_guard.py
REMOTE_BRIDGE=${B}/../../../../launch_outer_attempt4_formal_dbqps_v1_retry3/formal_job_outer_replay.py

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${V49}" 3688f00b0a32c490f5dd3781b26642f48361c3ba541256f9855730284016035c
require_sha "${V50}" 8e99713f2e19ab62567661c5264cb8cf8357241e3c610671beed521b35139f07
require_sha "${V52}" ec4611d755bcda77fb1c8b53d001b8d865afc4d9dcb8650ec48240ed7c373fc8
require_sha "${BOOTSTRAP}" 2856f01999235f645ddff401a196fe3d656df30b02361e9fcc8184ef8c6aaa0f
require_sha "${BASE_V7}" 8a65ab24f241931863608899b30c460e53532eda9607287edd1f5980f7e223fc
require_sha "${ROBUST_V9}" e717449a109669d6ed274e29d5aaf9f4285af8b37c586aeb0ad768970ba02693
require_sha "${REMOTE_BRIDGE}" 974634b25e4c421ef7674c357f19438a5c43441c498f44534cf97b86dca162e4

if pgrep -af "${B}.*([r]un_job_round8_strict_serial|[j]ob_formal_target10_batch_controller|[f]ormal_job_outer_replay|[f]ormal_pg_point_runner|[j]ob_fresh10_target10_autonomous)" >/dev/null; then
  echo "active JOB replay/controller detected; refusing v52 overlap" >&2
  exit 73
fi

test -d "${V50_ROOT}"
V50_STATE_SHA=$(sha256sum "${V50_ROOT}/package/formal_state.json" | awk '{print $1}')
test "$(tr -d '[:space:]' < "${V50_ROOT}/formal_search_accepted_counter.txt")" = 0

if ! test -f "${STATE}"; then
  test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = 395
  test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = 206
  python3 -u "${BOOTSTRAP}" > /tmp/job_v52r2_fresh0_bootstrap_output.json
  jq -e '
    .status == "complete" and .fresh_accepted_count == 0 and
    .fresh_gradient_observation_count == 0 and
    .fresh_range_is_baseline_point == true and
    .global_ordinal == 395 and .formal_replay_count == 206 and
    .v50_technical_physical_calls == 3 and
    .v50_countable_accepted_in_v52 == 0 and
    .old_roots_modified == false and
    .workload_calls == 0 and .explain_calls == 0
  ' /tmp/job_v52r2_fresh0_bootstrap_output.json >/dev/null
fi

test "$(jq -r '.campaign_contract' "${STATE}")" = job_v52r2_fresh0_v50_technical_excluded_remote974_v1
test "$(jq -r '.accepted_count' "${STATE}")" = "$(tr -d '[:space:]' < "${ACCEPTED}")"
test "$(sha256sum "${V50_ROOT}/package/formal_state.json" | awk '{print $1}')" = "${V50_STATE_SHA}"
STATE_SHA=$(sha256sum "${STATE}" | awk '{print $1}')
GLOBAL_BEFORE=$(tr -d '[:space:]' < "${B}/replay_counter.txt")
FORMAL_BEFORE=$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")
ACCEPTED_BEFORE=$(tr -d '[:space:]' < "${ACCEPTED}")

PREFLIGHT=${ROOT}/preflights/v52r2_preflight_${STATE_SHA}_${GLOBAL_BEFORE}.json
mkdir -p "$(dirname "${PREFLIGHT}")"
if ! test -f "${PREFLIGHT}"; then
  TEMP=${PREFLIGHT}.tmp.$$
  python3 -u "${V52}" --preflight-only > "${TEMP}"
  mv "${TEMP}" "${PREFLIGHT}"
fi
jq -e '
  .status == "passed" and .controller == "job_v52r2" and
  .new_workload_calls == 0 and .explain_calls == 0
' "${PREFLIGHT}" >/dev/null
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${STATE_SHA}"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = "${GLOBAL_BEFORE}"
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = "${FORMAL_BEFORE}"
test "$(tr -d '[:space:]' < "${ACCEPTED}")" = "${ACCEPTED_BEFORE}"
test "$(sha256sum "${V50_ROOT}/package/formal_state.json" | awk '{print $1}')" = "${V50_STATE_SHA}"
require_sha "${REMOTE_BRIDGE}" 974634b25e4c421ef7674c357f19438a5c43441c498f44534cf97b86dca162e4

exec python3 -u "${V52}"
