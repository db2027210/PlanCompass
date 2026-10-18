#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
ATTEMPT=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726
RUN=${ATTEMPT}/autonomous_target10_controller_v4/runs/autonomous_diagnostic_39
SUMMARY=${RUN}/top5_real_replay_round12_diagnostic_v1/summary.json
LEDGER=${RUN}/strict_top5_real_replay_round12_ledger_v1.json
FORMAL_STATE=${ATTEMPT}/formal10_tpcc_state.json
CONFIG=${ATTEMPT}/gradient_debug_round11_tpcc_calibrated_deadband_v1_20260728/config.json
CONTROLLER_STATE=${PROJECT}/history/tpcc_autonomous_target10_controller_v10_20260731/controller_state.json
REPAIR=${PROJECT}/tools/repair_tpcc_formal_range_invariant.py
EXPECTED_FORMAL_SHA=715a8fb788baa17ab6fa91fac1e9e3e0cb8dc360343c1848d79b1f7e16d035ba
EXPECTED_REPAIR_SHA=efd97bae906859d2269d531fa60d0e41f05f1db73b4e2646a07b11ad6b01eb66

test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = \
    "${EXPECTED_FORMAL_SHA}"
test "$(sha256sum "${REPAIR}" | awk '{print $1}')" = \
    "${EXPECTED_REPAIR_SHA}"
test -f "${SUMMARY}"
test -f "${LEDGER}"

jq -e '
    .real_workload_invocation_count == 5 and
    (.successful | length) == 5 and
    (.technical_failures | length) == 0
' "${LEDGER}" >/dev/null
jq -e '
    .baseline_params_restored == true and
    .baseline_snapshot_restored == true and
    .successful_real_replay_count == 5 and
    .technical_failure_count == 0
' "${SUMMARY}" >/dev/null
jq -e --arg root "${RUN}" '
    .current_run == null and
    .audits[-1].root == $root and
    .audits[-1].mode == "diagnostic" and
    .audits[-1].physical_calls == 5 and
    .audits[-1].valid_replays == 5 and
    .audits[-1].technical_failures == 0 and
    .audits[-1].clean == true and
    .audits[-1].baseline_restored == true
' "${CONTROLLER_STATE}" >/dev/null

exec python3 -u "${REPAIR}" \
    "${FORMAL_STATE}" \
    "${CONFIG}" \
    --expected-sha256 "${EXPECTED_FORMAL_SHA}" \
    --apply
