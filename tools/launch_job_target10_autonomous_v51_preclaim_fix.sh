#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
ROOT=${T}/autonomous_target10_v50_fresh0
STATE=${ROOT}/package/formal_state.json
FRESH_ACCEPTED=${ROOT}/formal_search_accepted_counter.txt
OLD_STATE=${T}/package/formal_state.json
OLD_ACCEPTED=${B}/formal_search_accepted_counter.txt
V49=/tmp/job_fresh10_target10_autonomous_v49.py
V50=/tmp/job_fresh10_target10_autonomous_v50.py
V51=/tmp/job_fresh10_target10_autonomous_v51.py
BASE_V7=/tmp/run_job_round8_strict_serial_base_v7_explicit_preclaim.py
ROBUST_V9=/tmp/run_job_round8_strict_serial_robust_v9_state_guard.py
BRIDGE=/tmp/formal_job_outer_replay_v51.py
DEPLOY=/tmp/deploy_job_v51_control_bridge.py

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${V49}" 3688f00b0a32c490f5dd3781b26642f48361c3ba541256f9855730284016035c
require_sha "${V50}" 6536262ab4edc0dd76e7c34a1517de9bbc0a93509706426a8973c1c88e61c428
require_sha "${V51}" 5e1f3beaa37bd88a872ebdff21ed440a99eb8b0b7324f5180d8521cf074f6f1d
require_sha "${BASE_V7}" 03c3cfce2aa0c6badaa9afb2dd3738026ac469d1d8e3e901066b37c1e3b4e0e2
require_sha "${ROBUST_V9}" e717449a109669d6ed274e29d5aaf9f4285af8b37c586aeb0ad768970ba02693
require_sha "${BRIDGE}" c53fbb20b401941ec83555bfd64211162bd873f0919558ae5394405af54a7b1b
require_sha "${DEPLOY}" 5969235043ccacc141762374764e24af04fbe51659dbb6932966d7f73227cd0b

if pgrep -af "${B}.*([r]un_job_round8_strict_serial|[j]ob_formal_target10_batch_controller|[f]ormal_job_outer_replay|[f]ormal_pg_point_runner|[j]ob_fresh10_target10_autonomous)" >/dev/null; then
  echo "active JOB replay/controller detected; refusing v51 overlap" >&2
  exit 73
fi

test -f "${STATE}"
test "$(jq -r '.accepted_count' "${STATE}")" = 0
test "$(tr -d '[:space:]' < "${FRESH_ACCEPTED}")" = 0
test "$(jq -r '.historical_accepted_excluded' "${STATE}")" = 3
test "$(tr -d '[:space:]' < "${OLD_ACCEPTED}")" = 3
OLD_STATE_SHA=$(sha256sum "${OLD_STATE}" | awk '{print $1}')
STATE_SHA=$(sha256sum "${STATE}" | awk '{print $1}')
GLOBAL_BEFORE=$(tr -d '[:space:]' < "${B}/replay_counter.txt")
FORMAL_BEFORE=$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")

python3 -u "${DEPLOY}" > /tmp/job_v51_control_bridge_deploy_output.json
jq -e '
  .status == "complete" and .workload_calls == 0 and
  .ordinal_claims == 0 and .mysql_touched == false and
  .target_sha256_after == "c53fbb20b401941ec83555bfd64211162bd873f0919558ae5394405af54a7b1b"
' /tmp/job_v51_control_bridge_deploy_output.json >/dev/null

PREFLIGHT=${ROOT}/preflights/v51_preflight_${STATE_SHA}_${GLOBAL_BEFORE}.json
mkdir -p "$(dirname "${PREFLIGHT}")"
if ! test -f "${PREFLIGHT}"; then
  TEMP=${PREFLIGHT}.tmp.$$
  python3 -u "${V51}" --preflight-only > "${TEMP}"
  mv "${TEMP}" "${PREFLIGHT}"
fi
jq -e '
  .status == "passed" and .controller == "job_v51" and
  .accepted_count == 0 and .new_workload_calls == 0 and .explain_calls == 0
' "${PREFLIGHT}" >/dev/null
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${STATE_SHA}"
test "$(sha256sum "${OLD_STATE}" | awk '{print $1}')" = "${OLD_STATE_SHA}"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = "${GLOBAL_BEFORE}"
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = "${FORMAL_BEFORE}"
test "$(tr -d '[:space:]' < "${FRESH_ACCEPTED}")" = 0
test "$(tr -d '[:space:]' < "${OLD_ACCEPTED}")" = 3

exec python3 -u "${V51}"
