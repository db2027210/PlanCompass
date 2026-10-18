#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: $0 preflight|run FORMAL_STATE_SHA GLOBAL_COUNTER FORMAL_REPLAY_COUNTER ACCEPTED_COUNTER" >&2
  exit 64
fi

mode=$1
expected_state_sha=$2
expected_global=$3
expected_formal_replay=$4
expected_accepted=$5

B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
ROOT=${T}/autonomous_target10_v57_fresh0
STATE=${ROOT}/package/formal_state.json
ACCEPTED=${ROOT}/formal_search_accepted_counter.txt
V65=/tmp/job_fresh10_target10_autonomous_v65.py

require_sha() {
  local path=$1
  local expected=$2
  test -f "$path"
  test "$(sha256sum "$path" | awk '{print $1}')" = "$expected"
}

case "$mode" in
  preflight|run) ;;
  *) echo "invalid mode: $mode" >&2; exit 64 ;;
esac

require_sha "$V65" 4a7af296ada667463a2b49d63fae01c12038251492b50da4e4f7d45f3798e228
require_sha /tmp/job_fresh10_target10_autonomous_v64.py cea34419a1c6ba790b98208dc8c80bbef51b0efdaae81865459b93638181422a
require_sha /tmp/job_fresh10_target10_autonomous_v63.py 15b7e1658d3328a3a92d2648ed610cf9153ec2e26e848b46fc2e756a30a74980
require_sha /tmp/run_job_round8_strict_serial_robust_v13_resource_eligibility.py d43309509d848683695b9efc72435ff2c05c44a27173ca77f9d34a3b76ebe407
require_sha /tmp/run_job_round8_strict_serial_robust_v12_eligibility.py f8a026053bf830bdb7e227a54876ee28aa83e23d30246cbd3ef519c5ef66be6b
require_sha /tmp/run_job_round8_strict_serial_robust_v11_gradient_state_guard.py d8919ef67c0b9fe0b9666717e54eedb8cf197b835d4a1e1a918c18674e1b3043
require_sha "$STATE" "$expected_state_sha"

if pgrep -af "${B}.*([r]un_job_round8_strict_serial|[j]ob_formal_target10_batch_controller|[f]ormal_job_outer_replay|[f]ormal_pg_point_runner|[j]ob_fresh10_target10_autonomous)" >/dev/null; then
  echo "active JOB replay/controller detected; refusing v65 overlap" >&2
  exit 73
fi

test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = "$expected_global"
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = "$expected_formal_replay"
test "$(tr -d '[:space:]' < "$ACCEPTED")" = "$expected_accepted"
jq -e --argjson accepted "$expected_accepted" '
  .accepted_count == $accepted and
  .accepted_count < 10 and
  (.incumbent_score | numbers)
' "$STATE" >/dev/null

mkdir -p "${ROOT}/preflights"
PREFLIGHT=${ROOT}/preflights/v65_preflight_$(date +%Y%m%dT%H%M%S)_$$.json
TEMP=${PREFLIGHT}.tmp
python3 -u "$V65" --preflight-only > "$TEMP"
mv "$TEMP" "$PREFLIGHT"
jq -e --argjson accepted "$expected_accepted" '
  .status == "passed" and .controller == "job_v65" and
  .accepted_count == $accepted and .new_workload_calls == 0 and
  .explain_calls == 0
' "$PREFLIGHT" >/dev/null
require_sha "$STATE" "$expected_state_sha"
test "$(tr -d '[:space:]' < "${B}/replay_counter.txt")" = "$expected_global"
test "$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")" = "$expected_formal_replay"
test "$(tr -d '[:space:]' < "$ACCEPTED")" = "$expected_accepted"

if [[ "$mode" == preflight ]]; then
  jq . "$PREFLIGHT"
  exit 0
fi

exec python3 -u "$V65"
