#!/usr/bin/env bash
set -euo pipefail

PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
V40_ROOT=${BASE}/tpch_final_algorithm_fresh0_v40_retest_compat_20260803
V40_LOOP=${V40_ROOT}/target10_loop_v40
CYCLE=${V40_LOOP}/cycle_004
V41_ROOT=${BASE}/tpch_final_algorithm_fresh0_v41_robust4_handoff_20260803
ANCHOR=${BASE}/tpch_v38_current_anchor_revalidation_20260803/current_anchor_state.v2.json
V40_RUNNER=/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v40.py
V42_RUNNER=/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v42.py
V42_CONTROLLER=/tmp/tpch_v42_complete_cycle004_diagnostics.py
V26=/tmp/tpch_fresh0_target10_loop_v36_base.py
V32=/tmp/tpch_fresh10_target10_loop_v32_v6_hierarchical.py
EXACT_BUILDER=/tmp/tpch_fresh0_handover_v19/build_tpch_exact_plan_repeat_validation.py
ROBUST_BUILDER=/tmp/build_tpch_two_observation_robust_repeat_v3.py

require_sha() {
  local file=$1
  local expected=$2
  test -f "${file}"
  test "$(sha256sum "${file}" | awk '{print $1}')" = "${expected}"
}

require_sha "${V40_RUNNER}" c3d352b980daf403a3344b7381ad5699215f6b2ba59b2a40c10b6e71f91a908d
require_sha "${V42_RUNNER}" 5f6ad0c1afc5bfe0adbb8f93aef9de2e487c1bae5e648fe9c59c793053e9e248
require_sha "${V42_CONTROLLER}" 8e2ba0f8d9e48bfe577d4b63c30e876967b8ad49f43ec4daff9f30722f8114fa
require_sha "${V26}" 34107e91b4bcfd0b2fe8d2911a0855e1512fc881665f4db3c3cb67618bd055fb
require_sha "${V32}" 54ac22227f190bfbbc1f2889927cba9500cbe1bd6f4347287d69c3647d6e98b5
require_sha "${EXACT_BUILDER}" 4f1684e73c9c91f32d94655053a4c6af5a8f8f0d2d2eb8a94363eaa24b48cfe7
require_sha "${ROBUST_BUILDER}" b124ef363035606c55bcc16a801c220b50725b18d3cbe8e98fe816fdf83195b5

test -d "${V40_ROOT}"
test -d "${CYCLE}"
test -d "${V41_ROOT}"
test "$(tr -d '[:space:]' < "${V40_ROOT}/formal_search_accepted_counter.txt")" = 0
test "$(tr -d '[:space:]' < "${V41_ROOT}/formal_search_accepted_counter.txt")" = 0

if pgrep -af "[t]pch_fresh0_target10_v38_bounded_retest.py" >/dev/null; then
  echo "broken v40 parent controller is unexpectedly still active" >&2
  exit 73
fi
if ! pgrep -af "[t]pch_v41_preformal_handoff.py" >/dev/null; then
  echo "v41 handoff is not waiting; refusing orphan v42 diagnostics" >&2
  exit 74
fi
if pgrep -af "[r]un_tpch_round29_diagnostic_exact_plan_regime_batch|[r]un_tpch_weighted_candidate_attempt48|[r]un_tpch_formal_exact_plan_regime_batch" >/dev/null; then
  echo "active TPC-H replay detected; refusing overlap" >&2
  exit 75
fi

jq -e '
  .status == "complete" and .diagnostic_only == true and
  .accepted_count == 0 and .physical_call_count == 5 and
  .valid_replay_count == 5 and .technical_failure_count == 0 and
  .baseline_restored == true and .accepted_update_allowed == false and
  .incumbent_update_allowed == false and .range_update_allowed == false and
  .gradient_update_allowed == false
' "${CYCLE}/diagnostic_probe/diagnostic_state.json" >/dev/null

ANCHOR_SHA=$(sha256sum "${ANCHOR}" | awk '{print $1}')
"${PYTHON}" -u "${V42_CONTROLLER}" \
  --current-anchor-state "${ANCHOR}" \
  --current-anchor-state-sha256 "${ANCHOR_SHA}" \
  --preflight-only

test "$(tr -d '[:space:]' < "${V40_ROOT}/formal_search_accepted_counter.txt")" = 0
test "$(tr -d '[:space:]' < "${V41_ROOT}/formal_search_accepted_counter.txt")" = 0
test "$(sha256sum "${ANCHOR}" | awk '{print $1}')" = "${ANCHOR_SHA}"

exec "${PYTHON}" -u "${V42_CONTROLLER}" \
  --current-anchor-state "${ANCHOR}" \
  --current-anchor-state-sha256 "${ANCHOR_SHA}"
