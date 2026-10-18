#!/usr/bin/env bash
set -euo pipefail

PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
OLD_FRESH=${BASE}/tpch_final_algorithm_fresh10_v58_20260731T2340
V37_ROOT=${BASE}/tpch_final_algorithm_fresh0_v37_feasible_ladder_20260803
SOURCE_PACKAGE=${BASE}/gradient_debug_round57_probe5_recovery_v18_20260731T2310_package

ANCHOR_RUNNER=/tmp/run_tpch_current_anchor_revalidation_v38.py
ANCHOR_RESEALER=/tmp/reseal_tpch_v38_anchor_state_v2.py
WRAPPER=/tmp/tpch_fresh0_target10_v38_bounded_retest.py
LOOP_BASE=/tmp/tpch_fresh0_target10_loop_v36_base.py
EXPANDER=/tmp/tpch_exact_regime_neighbor_expand_v5_bounded.py
BUILDER=/tmp/build_tpch_autonomous_successor_top5_v9_bounded_retest.py
COMPAT_RUNNER=/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v40.py

SOURCE_FORMAL=${OLD_FRESH}/target10_loop_v26/cycle_011/formal_run/formal_state.json
ACCEPTED_COUNTER=${V37_ROOT}/formal_search_accepted_counter.txt
RUNNER_MODULE=${SOURCE_PACKAGE}/run_tpch_round29_diagnostic_exact_plan_regime_batch.py
WORKLOAD=${SOURCE_PACKAGE}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh
WORKLOAD_DRIVER=${SOURCE_PACKAGE}/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py
FORMAL_MEASUREMENT=${SOURCE_PACKAGE}/formal_measurement.py
FORMAL_CONTRACTS=${SOURCE_PACKAGE}/formal_contracts.py
RESOURCE_MONITOR=${SOURCE_PACKAGE}/system_resource_monitor.py
CONFIG=${OLD_FRESH}/config_fresh0.json
ANCHOR_ROOT=${BASE}/tpch_v38_current_anchor_revalidation_20260803
ANCHOR_RAW_STATE=${ANCHOR_ROOT}/current_anchor_state.json
ANCHOR_STATE=${ANCHOR_ROOT}/current_anchor_state.v2.json
ANCHOR_DIAGNOSTIC_STATE=${ANCHOR_ROOT}/diagnostic_state.json

test "$(sha256sum "${ANCHOR_RUNNER}" | awk '{print $1}')" = "f76b26d85b9489a7b9f825fe940f8e173f71e4859cb8f14c94cc5d944655da7f"
test "$(sha256sum "${ANCHOR_RESEALER}" | awk '{print $1}')" = "e6524aa6074303c2236a52072b48e19080031d54971768e1b2768606f4c74312"
test "$(sha256sum "${WRAPPER}" | awk '{print $1}')" = "589908b51513b7dc0b2d2baa6a636b7bce7705e81173a4fdb234a3a8e4f67e71"
test "$(sha256sum "${LOOP_BASE}" | awk '{print $1}')" = "34107e91b4bcfd0b2fe8d2911a0855e1512fc881665f4db3c3cb67618bd055fb"
test "$(sha256sum "${EXPANDER}" | awk '{print $1}')" = "dce066d239ca68eb95ec10bd39a77e798212395df782ae850ffe7d27d5346a78"
test "$(sha256sum "${BUILDER}" | awk '{print $1}')" = "3f2514db76a9e86484029b2bd72162c96f190f5f71af218543335e1b4c93208a"
test "$(sha256sum "${COMPAT_RUNNER}" | awk '{print $1}')" = "c3d352b980daf403a3344b7381ad5699215f6b2ba59b2a40c10b6e71f91a908d"
test "$(sha256sum "${SOURCE_FORMAL}" | awk '{print $1}')" = "af8b17dc77b82fb60166102d091d112eae132c4a6f086fb5a560316385e544ee"
test "$(sha256sum "${RUNNER_MODULE}" | awk '{print $1}')" = "12b6ade0325541dbe1eadfd40af873fd3144fc1cbf97d9eca52f11a430731872"
test "$(sha256sum "${WORKLOAD}" | awk '{print $1}')" = "25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008"
test "$(sha256sum "${WORKLOAD_DRIVER}" | awk '{print $1}')" = "31c0080ec1487d0f4cb7a840c2584d890c5e780f561f6c4a480099ba029d80eb"
test "$(sha256sum "${FORMAL_MEASUREMENT}" | awk '{print $1}')" = "90feb3b7b0e9c44a820e4c9c1ff76f6204a22de0b49d9059e06615f0bb1641c6"
test "$(sha256sum "${FORMAL_CONTRACTS}" | awk '{print $1}')" = "fca62960cf6328feb7528a0ecfe2e6a01e3072ff9a79742eafd6d0759dec1d8d"
test "$(sha256sum "${RESOURCE_MONITOR}" | awk '{print $1}')" = "c960dee360f9de2eada3908f87afaafe2290a13ccfc906cca09352bbdfcfff16"
test "$(sha256sum "${CONFIG}" | awk '{print $1}')" = "0708698830d20441c2dcceed1ccc98f66527c41cdad6d4d577011bfc5f93da06"
test "$(sed -n '1p' "${ACCEPTED_COUNTER}")" = "0"

if test ! -f "${ANCHOR_RAW_STATE}"; then
    "${PYTHON}" -u "${ANCHOR_RUNNER}" \
        --source-formal-state "${SOURCE_FORMAL}" \
        --source-formal-state-sha256 af8b17dc77b82fb60166102d091d112eae132c4a6f086fb5a560316385e544ee \
        --accepted-counter "${ACCEPTED_COUNTER}" \
        --runner-module "${RUNNER_MODULE}" \
        --runner-module-sha256 12b6ade0325541dbe1eadfd40af873fd3144fc1cbf97d9eca52f11a430731872 \
        --workload "${WORKLOAD}" \
        --workload-sha256 25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008 \
        --workload-driver "${WORKLOAD_DRIVER}" \
        --workload-driver-sha256 31c0080ec1487d0f4cb7a840c2584d890c5e780f561f6c4a480099ba029d80eb \
        --runtime-dependency "${FORMAL_MEASUREMENT}::90feb3b7b0e9c44a820e4c9c1ff76f6204a22de0b49d9059e06615f0bb1641c6" \
        --runtime-dependency "${FORMAL_CONTRACTS}::fca62960cf6328feb7528a0ecfe2e6a01e3072ff9a79742eafd6d0759dec1d8d" \
        --runtime-dependency "${RESOURCE_MONITOR}::c960dee360f9de2eada3908f87afaafe2290a13ccfc906cca09352bbdfcfff16" \
        --config "${CONFIG}" \
        --config-sha256 0708698830d20441c2dcceed1ccc98f66527c41cdad6d4d577011bfc5f93da06 \
        --container pg5_tpch_ap_slot_node01_20260726 \
        --database template_fullgrid_tpch_pg5 \
        --output "${ANCHOR_ROOT}" \
        --anchor-mode default \
        --timeout-seconds 900 \
        --maximum-score-span 0.05 \
        --maximum-resource-factor 1.5 \
        --armed
fi

RAW_ANCHOR_SHA=$(sha256sum "${ANCHOR_RAW_STATE}" | awk '{print $1}')
ANCHOR_DIAGNOSTIC_SHA=$(sha256sum "${ANCHOR_DIAGNOSTIC_STATE}" | awk '{print $1}')
if test ! -f "${ANCHOR_STATE}"; then
    "${PYTHON}" -u "${ANCHOR_RESEALER}" \
        --source-formal-state "${SOURCE_FORMAL}" \
        --source-formal-state-sha256 af8b17dc77b82fb60166102d091d112eae132c4a6f086fb5a560316385e544ee \
        --diagnostic-state "${ANCHOR_DIAGNOSTIC_STATE}" \
        --diagnostic-state-sha256 "${ANCHOR_DIAGNOSTIC_SHA}" \
        --raw-anchor-state "${ANCHOR_RAW_STATE}" \
        --raw-anchor-state-sha256 "${RAW_ANCHOR_SHA}" \
        --anchor-builder "${ANCHOR_RUNNER}" \
        --anchor-builder-sha256 f76b26d85b9489a7b9f825fe940f8e173f71e4859cb8f14c94cc5d944655da7f \
        --output "${ANCHOR_STATE}"
fi

ANCHOR_SHA=$(sha256sum "${ANCHOR_STATE}" | awk '{print $1}')
jq -e --arg raw_anchor_sha "${RAW_ANCHOR_SHA}" '
    .status == "complete" and
    .diagnostic_only == true and
    .physical_call_count == 2 and
    .valid_replay_count == 2 and
    .technical_failure_count == 0 and
    .accepted_count == 0 and
    .baseline_restored == true and
    .formal_state_mutation_allowed == false and
    .accepted_update_allowed == false and
    .incumbent_update_allowed == false and
    .range_update_allowed == false and
    .gradient_update_allowed == false and
    .source_formal_state_mutated == false and
    .accepted_counter_mutated == false and
    .current_anchor_state_sha256 == $raw_anchor_sha
' "${ANCHOR_DIAGNOSTIC_STATE}" >/dev/null
jq -e '
    .accepted_count == 0 and
    .current_epoch_anchor_revalidation.observation_count == 2 and
    .current_epoch_anchor_revalidation.state_neutral == true and
    .current_epoch_anchor_revalidation.formal_source_mutated == false and
    .current_epoch_anchor_revalidation.zero_call_reseal_contract ==
        "tpch_v38_anchor_zero_call_reseal_v2" and
    .current_epoch_anchor_revalidation.new_physical_calls == 0 and
    ([.current_epoch_anchor_revalidation.observations[] |
        .baseline_restored_after_observation] | all)
' "${ANCHOR_STATE}" >/dev/null
test "$(sha256sum "${SOURCE_FORMAL}" | awk '{print $1}')" = "af8b17dc77b82fb60166102d091d112eae132c4a6f086fb5a560316385e544ee"
test "$(sed -n '1p' "${ACCEPTED_COUNTER}")" = "0"

"${PYTHON}" -u "${WRAPPER}" \
    --current-anchor-state "${ANCHOR_STATE}" \
    --current-anchor-state-sha256 "${ANCHOR_SHA}" \
    --preflight-only

exec "${PYTHON}" -u "${WRAPPER}" \
    --current-anchor-state "${ANCHOR_STATE}" \
    --current-anchor-state-sha256 "${ANCHOR_SHA}"
