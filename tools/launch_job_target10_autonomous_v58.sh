#!/usr/bin/env bash
set -euo pipefail

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
ROOT=${T}/autonomous_target10_v57_fresh0
STATE=${ROOT}/package/formal_state.json
ACCEPTED=${ROOT}/formal_search_accepted_counter.txt
V58=/tmp/job_fresh10_target10_autonomous_v58.py
ROBUST_V11=/tmp/run_job_round8_strict_serial_robust_v11_gradient_state_guard.py
FORMAL_V2=/tmp/job_formal_target10_batch_controller_v2_gradient_commit.py
FORMAL_V1=/tmp/job_formal_target10_batch_controller.py
REPAIR=/tmp/repair_job_v57_to_v58_gradient_state.py
REPAIR_TX=${ROOT}/package/v57_to_v58_gradient_repair_transaction.json
REPAIR_AUDIT=${ROOT}/package/v57_to_v58_gradient_repair_audit.json

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${V58}" 8ed24530017d866c1c9ca3ce31e5d4e69a1c7083b6237827c274c96093623a30
require_sha "${ROBUST_V11}" d8919ef67c0b9fe0b9666717e54eedb8cf197b835d4a1e1a918c18674e1b3043
require_sha "${FORMAL_V2}" e5e4a090b3731add51005c00996cb2af7c450a3c491708321cc96d5d74cf294b
require_sha "${FORMAL_V1}" 3c9246b67a7fbaf5e28115efc59e4ac68066fc96e6515b6524099ca804c69668
require_sha "${REPAIR}" 6a43da37c73a2601bd7f57c77798dc751316ecfd82c861e9b810d90b9a588399

if pgrep -af "${B}.*([r]un_job_round8_strict_serial|[j]ob_formal_target10_batch_controller|[f]ormal_job_outer_replay|[f]ormal_pg_point_runner|[j]ob_fresh10_target10_autonomous)" >/dev/null; then
  echo "active JOB replay/controller detected; refusing v58 overlap" >&2
  exit 73
fi

test -f "${STATE}"
test -f "${ACCEPTED}"
test -f "${REPAIR_TX}"
test -f "${REPAIR_AUDIT}"
jq -e '
  .status == "committed" and
  .contract == "job_v57_to_v58_zero_call_gradient_repair_v1" and
  .new_workload_calls == 0 and .counter_updates == 0
' "${REPAIR_TX}" >/dev/null
jq -e '
  .status == "complete" and
  .contract == "job_v57_to_v58_zero_call_gradient_repair_v1" and
  .new_workload_calls == 0 and .counter_updates == 0
' "${REPAIR_AUDIT}" >/dev/null
test "$(jq -r '.campaign_contract' "${STATE}")" = job_v57_fresh0_resource_median_no_retry_v1
test "$(jq -r '.accepted_count' "${STATE}")" = "$(tr -d '[:space:]' < "${ACCEPTED}")"

STATE_SHA=$(sha256sum "${STATE}" | awk '{print $1}')
GLOBAL_BEFORE=$(tr -d '[:space:]' < "${B}/replay_counter.txt")
FORMAL_BEFORE=$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")
ACCEPTED_BEFORE=$(tr -d '[:space:]' < "${ACCEPTED}")
PREFLIGHT=${ROOT}/preflights/v58_preflight_${STATE_SHA}_${GLOBAL_BEFORE}.json
mkdir -p "$(dirname "${PREFLIGHT}")"
if ! test -f "${PREFLIGHT}"; then
  TEMP=${PREFLIGHT}.tmp.$$
  python3 -u "${V58}" --preflight-only > "${TEMP}"
  mv "${TEMP}" "${PREFLIGHT}"
fi
jq -e --argjson accepted "${ACCEPTED_BEFORE}" '
  .status == "passed" and .controller == "job_v58" and
  .accepted_count == $accepted and .new_workload_calls == 0 and .explain_calls == 0
' "${PREFLIGHT}" >/dev/null
test "$(sha256sum "${STATE}" | awk '{print $1}')" = "${STATE_SHA}"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = "${GLOBAL_BEFORE}"
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = "${FORMAL_BEFORE}"
test "$(tr -d '[:space:]' < "${ACCEPTED}")" = "${ACCEPTED_BEFORE}"

exec python3 -u "${V58}"
