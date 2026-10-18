#!/usr/bin/env bash
set -euo pipefail

# Start a separate fresh0 campaign only after v49 and its point runner have
# exited.  Old accepted=3 remains immutable historical/training evidence.
B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
OLD_STATE=${T}/package/formal_state.json
OLD_ACCEPTED_COUNTER=${B}/formal_search_accepted_counter.txt
ROOT=${T}/autonomous_target10_v50_fresh0
STATE=${ROOT}/package/formal_state.json
ACCEPTED_COUNTER=${ROOT}/formal_search_accepted_counter.txt
V49=/tmp/job_fresh10_target10_autonomous_v49.py
V50=/tmp/job_fresh10_target10_autonomous_v50.py
BOOTSTRAP=/tmp/bootstrap_job_v50_fresh0.py

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${V49}" 3688f00b0a32c490f5dd3781b26642f48361c3ba541256f9855730284016035c
require_sha "${V50}" 3700e33f48d0391d78fc4079dc526f0143edb99f7b2dc9db9de31153798a3508
require_sha "${BOOTSTRAP}" 2cf636a047dd9acb08bfe881cae2cd21dc559e5c42b1248a913c7378bd596edd

if pgrep -af "${B}.*([r]un_job_round8_strict_serial|[j]ob_formal_target10_batch_controller|[f]ormal_job_outer_replay|[f]ormal_pg_point_runner|[j]ob_fresh10_target10_autonomous)" >/dev/null; then
  echo "active JOB replay/controller detected; refusing overlap" >&2
  exit 73
fi

OLD_STATE_SHA=$(sha256sum "${OLD_STATE}" | awk '{print $1}')
test "$(tr -d '[:space:]' < "${OLD_ACCEPTED_COUNTER}")" = 3
BOOTSTRAP_OUTPUT=/tmp/job_v50_fresh0_bootstrap_output.json
python3 -u "${BOOTSTRAP}" > "${BOOTSTRAP_OUTPUT}"
jq -e '
  .status == "complete" and
  .historical_accepted_excluded == 3 and .fresh_accepted_count == 0 and
  .fresh_incumbent_score == 4.416709205648302 and
  .fresh_range_is_baseline_point == true and
  .historical_incumbent_or_range_inherited == false and
  .old_state_or_accepted_counter_modified == false and
  .workload_calls == 0 and .explain_calls == 0
' "${BOOTSTRAP_OUTPUT}" >/dev/null
test "$(sha256sum "${OLD_STATE}" | awk '{print $1}')" = "${OLD_STATE_SHA}"
test "$(tr -d '[:space:]' < "${OLD_ACCEPTED_COUNTER}")" = 3

STATE_SHA_BEFORE=$(sha256sum "${STATE}" | awk '{print $1}')
GLOBAL_BEFORE=$(tr -d '[:space:]' < "${B}/replay_counter.txt")
FORMAL_BEFORE=$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")
ACCEPTED_BEFORE=$(tr -d '[:space:]' < "${ACCEPTED_COUNTER}")
test "${ACCEPTED_BEFORE}" = 0
test "$(jq -r '.accepted_count' "${STATE}")" = "${ACCEPTED_BEFORE}"
test "$(jq -r '.formal_replay_count' "${STATE}")" = "${FORMAL_BEFORE}"
test "$(jq -r '.global_ordinal' "${STATE}")" = "${GLOBAL_BEFORE}"
test "$(jq -r '.historical_accepted_excluded' "${STATE}")" = 3

PREFLIGHT=${ROOT}/preflights/v50_preflight_${STATE_SHA_BEFORE}_${GLOBAL_BEFORE}.json
mkdir -p "$(dirname "${PREFLIGHT}")"
if ! test -f "${PREFLIGHT}"; then
  TEMP=${PREFLIGHT}.tmp.$$
  python3 -u "${V50}" --preflight-only > "${TEMP}"
  mv "${TEMP}" "${PREFLIGHT}"
fi
jq -e '
  .status == "passed" and .controller == "job_v50" and
  .new_workload_calls == 0 and .explain_calls == 0
' "${PREFLIGHT}" >/dev/null
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${STATE_SHA_BEFORE}"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = "${GLOBAL_BEFORE}"
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = "${FORMAL_BEFORE}"
test "$(tr -d '[:space:]' < "${ACCEPTED_COUNTER}")" = "${ACCEPTED_BEFORE}"
test "$(sha256sum "${OLD_STATE}" | awk '{print $1}')" = "${OLD_STATE_SHA}"
test "$(tr -d '[:space:]' < "${OLD_ACCEPTED_COUNTER}")" = 3

exec python3 -u "${V50}"
