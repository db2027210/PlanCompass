#!/usr/bin/env bash
set -euo pipefail

# Launch only after the already-running v47/v46 chain has committed formal004
# and exited.  This wrapper performs a zero-workload-call preflight, then hands
# control to the idempotent target10 successor controller.
B=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
T=${B}/job_final_algorithm_fresh10_v36_20260731T2020
P=${B}/job_round8_corrected_top5_v6_20260731
FORMAL=${T}/autonomous_v45/formal_batch004
STATE=${T}/package/formal_state.json
ACCEPTED_COUNTER=${B}/formal_search_accepted_counter.txt

TARGET10=/tmp/job_fresh10_target10_autonomous_v49.py
GENERATOR=/tmp/job_exact_regime_successor_expand.py
REPEAT_BUILDER=/tmp/build_job_exact_plan_repeat_validation.py
GATE=/tmp/evaluate_job_clean5_gate.py
ROBUST_BUILDER=/tmp/build_job_three_observation_formal_selection_v1.py
ROBUST_RUNNER_V7=/tmp/run_job_round8_strict_serial_robust_v7.py
ROBUST_RUNNER=/tmp/run_job_round8_strict_serial_robust_v8_apply_failure.py
FORMAL_CONTROLLER=/tmp/job_formal_target10_batch_controller.py
BASE_RUNNER=${P}/run_job_round8_strict_serial.py
CONTRACT=${T}/package/job_round8_strict_replay_contract.py

require_sha() {
  local path=$1
  local expected=$2
  test -f "${path}"
  test "$(sha256sum "${path}" | awk '{print $1}')" = "${expected}"
}

require_sha "${TARGET10}" 3688f00b0a32c490f5dd3781b26642f48361c3ba541256f9855730284016035c
require_sha "${GENERATOR}" a03c2264cdfe2b26a8bb4a5bcbc5648498ce68020806d6d4a84e3cb592b51da5
require_sha "${REPEAT_BUILDER}" 89c3a2238830087c129cadee76cd5a28248d87ce10b0fd98c6d5f9cb62bdd6cf
require_sha "${GATE}" 72700f5169ca7ba3bd1b527397ac179036ab8bebebb5fb7e977f5be4de2b558e
require_sha "${ROBUST_BUILDER}" 40c528bb43ecd6b31987daa6d0dec2704fde93da11d510261d854d9eceb532a7
require_sha "${ROBUST_RUNNER_V7}" 9251d7fa68276113656826bd6df1777c7b7dfe71b42d012674fbd19ca4b2f09c
require_sha "${ROBUST_RUNNER}" 5d965cd9410fba78486856dc2d78bd594b9ebf1d17eda4a8f593a5f43d6fa857
require_sha "${FORMAL_CONTROLLER}" 3c9246b67a7fbaf5e28115efc59e4ac68066fc96e6515b6524099ca804c69668
require_sha "${BASE_RUNNER}" 9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1
require_sha "${CONTRACT}" a5ae7fddcfcd16e1715e35cc5a67c3d3d135b2542ff0e132a4cca5817afddf05

TRANSACTION=${FORMAL}/replay/formal_state_commit_transaction.json
SUMMARY=${FORMAL}/replay/summary.json
test -f "${TRANSACTION}"
test -f "${SUMMARY}"
jq -e '
  .status == "committed" and
  (.accepted_after >= 0 and .accepted_after <= 10) and
  (.accepted_after >= .accepted_before)
' "${TRANSACTION}" >/dev/null
jq -e '
  .baseline_params_restored == true and
  (.physical_invocation_count >= 0 and .physical_invocation_count <= 5) and
  (.successful_real_replay_count + .technical_failure_count <= 5) and
  (.successful_real_replay_count <= .physical_invocation_count) and
  (.physical_invocation_count <=
    .successful_real_replay_count + .technical_failure_count) and
  .three_observation_acceptance_contract ==
    "job_three_independent_exact_point_median_acceptance_v2"
' "${SUMMARY}" >/dev/null

STATE_SHA=$(sha256sum "${STATE}" | awk '{print $1}')
TRANSACTION_STATE_SHA=$(jq -r '.state_sha256_after' "${TRANSACTION}")
STATE_ACCEPTED=$(jq -r '.accepted_count' "${STATE}")
STATE_GLOBAL=$(jq -r '.global_ordinal' "${STATE}")
STATE_FORMAL_REPLAY=$(jq -r '.formal_replay_count' "${STATE}")
TRANSACTION_ACCEPTED=$(jq -r '.accepted_after' "${TRANSACTION}")
TRANSACTION_GLOBAL=$(jq -r '.global_ordinal_after' "${TRANSACTION}")
TRANSACTION_FORMAL_REPLAY=$(jq -r '.formal_replay_count_after' "${TRANSACTION}")
COUNTER_ACCEPTED=$(tr -d '[:space:]' < "${ACCEPTED_COUNTER}")
COUNTER_GLOBAL=$(tr -d '[:space:]' < "${B}/replay_counter.txt")
COUNTER_FORMAL_REPLAY=$(tr -d '[:space:]' < "${B}/formal_search_replay_counter.txt")
test "${STATE_ACCEPTED}" = "${COUNTER_ACCEPTED}"
EXISTING_CYCLE=$(find "${T}/autonomous_target10_v49" -maxdepth 1 -type d -name 'cycle_[0-9][0-9][0-9]' -print -quit 2>/dev/null || true)
if test -z "${EXISTING_CYCLE}"; then
  test "${STATE_SHA}" = "${TRANSACTION_STATE_SHA}"
  test "${STATE_ACCEPTED}" = "${TRANSACTION_ACCEPTED}"
  test "${STATE_GLOBAL}" = "${TRANSACTION_GLOBAL}"
  test "${STATE_FORMAL_REPLAY}" = "${TRANSACTION_FORMAL_REPLAY}"
  test "${STATE_GLOBAL}" = "${COUNTER_GLOBAL}"
fi
test "${STATE_FORMAL_REPLAY}" = "${COUNTER_FORMAL_REPLAY}"

# Do not overlap the old v47/v46 chain, a point runner, or another JOB target10
# controller.  The expression is scoped to the JOB-only campaign path.
if pgrep -af "${B}.*([r]un_job_round8_strict_serial|[j]ob_formal_target10_batch_controller|[f]ormal_pg_point_runner|[j]ob_fresh10_target10_autonomous)" >/dev/null; then
  echo "active JOB replay/controller detected; refusing overlap" >&2
  exit 73
fi

PREFLIGHT_DIR=${T}/autonomous_target10_v49/preflights
mkdir -p "${PREFLIGHT_DIR}"
PREFLIGHT=${PREFLIGHT_DIR}/preflight_${STATE_SHA}.json
if test -f "${PREFLIGHT}"; then
  test "$(jq -r '.formal_state_sha256' "${PREFLIGHT}")" = "${STATE_SHA}"
else
  PREFLIGHT_TMP=${PREFLIGHT}.tmp.$$
  python3 -u "${TARGET10}" --preflight-only > "${PREFLIGHT_TMP}"
  mv "${PREFLIGHT_TMP}" "${PREFLIGHT}"
fi
jq -e '
  .status == "passed" and
  .new_workload_calls == 0 and .explain_calls == 0 and
  .manual_candidate_injection == false and
  .qps_used_for_inner_scoring == false
' "${PREFLIGHT}" >/dev/null

exec python3 -u "${TARGET10}"
