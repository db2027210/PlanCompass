#!/usr/bin/env bash
set -euo pipefail

base=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
package_root="$base/gradient_debug_round45_topology_v2_corrected_validation_v3_20260731T1550_package"
formal_root="$base/formal10_exact_plan_regime_round28_feedback_v1_20260729T2055_run"
output="$base/gradient_debug_round45_protocol_migration_v3_20260731T1550"
python_bin=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11

test ! -e "$output"
test "$(sha256sum "$formal_root/formal_state.json" | awk '{print $1}')" = \
    610a58fd17004c39182fa6fb95da607fa1be95e7e13f85cc88baff07c84798f5
test "$(sha256sum "$package_root/tpch_revalidate_migrate_formal_state_v2.py" | awk '{print $1}')" = \
    6ceb38fca21e0471aa8de9e39c9604c04f92819e3657f57919b121fd9daa3112
test "$(sha256sum "$package_root/run_tpch_round29_diagnostic_exact_plan_regime_batch.py" | awk '{print $1}')" = \
    7ab3449c70845732f19f8beeb0a714b6cc57fc3c065cc5c657215b31c93b507c
test "$(sha256sum "$package_root/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh" | awk '{print $1}')" = \
    25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008
test "$(sha256sum "$package_root/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py" | awk '{print $1}')" = \
    31c0080ec1487d0f4cb7a840c2584d890c5e780f561f6c4a480099ba029d80eb
test "$(sha256sum "$package_root/config.json" | awk '{print $1}')" = \
    0b6be9e78681028b730682e87bf01a76306169336e921c60b03bd7c0dfd69b92
test "$(sha256sum "$package_root/formal_measurement.py" | awk '{print $1}')" = \
    90feb3b7b0e9c44a820e4c9c1ff76f6204a22de0b49d9059e06615f0bb1641c6
test "$(sha256sum "$package_root/formal_contracts.py" | awk '{print $1}')" = \
    fca62960cf6328feb7528a0ecfe2e6a01e3072ff9a79742eafd6d0759dec1d8d
test "$(sha256sum "$package_root/system_resource_monitor.py" | awk '{print $1}')" = \
    c960dee360f9de2eada3908f87afaafe2290a13ccfc906cca09352bbdfcfff16

exec "$python_bin" -u \
    "$package_root/tpch_revalidate_migrate_formal_state_v2.py" \
    --formal-state "$formal_root/formal_state.json" \
    --formal-state-sha256 \
        610a58fd17004c39182fa6fb95da607fa1be95e7e13f85cc88baff07c84798f5 \
    --runner-module \
        "$package_root/run_tpch_round29_diagnostic_exact_plan_regime_batch.py" \
    --runner-module-sha256 \
        7ab3449c70845732f19f8beeb0a714b6cc57fc3c065cc5c657215b31c93b507c \
    --workload \
        "$package_root/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh" \
    --workload-sha256 \
        25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008 \
    --workload-driver \
        "$package_root/run_pg_weighted_sql_baseline_profile_rolling_v4_completiongate.py" \
    --workload-driver-sha256 \
        31c0080ec1487d0f4cb7a840c2584d890c5e780f561f6c4a480099ba029d80eb \
    --runtime-dependency \
        "$package_root/formal_measurement.py::90feb3b7b0e9c44a820e4c9c1ff76f6204a22de0b49d9059e06615f0bb1641c6" \
    --runtime-dependency \
        "$package_root/formal_contracts.py::fca62960cf6328feb7528a0ecfe2e6a01e3072ff9a79742eafd6d0759dec1d8d" \
    --runtime-dependency \
        "$package_root/system_resource_monitor.py::c960dee360f9de2eada3908f87afaafe2290a13ccfc906cca09352bbdfcfff16" \
    --config "$package_root/config.json" \
    --config-sha256 \
        0b6be9e78681028b730682e87bf01a76306169336e921c60b03bd7c0dfd69b92 \
    --container pg5_tpch_ap_slot_node01_20260726 \
    --database template_fullgrid_tpch_pg5 \
    --output "$output" \
    --timeout-seconds 1200 \
    --expected-accepted-count 1 \
    --armed
