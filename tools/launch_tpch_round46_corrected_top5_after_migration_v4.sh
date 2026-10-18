#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
PACKAGE=${BASE}/gradient_debug_round45_topology_v2_corrected_validation_v3_20260731T1550_package
OUTPUT=${BASE}/gradient_debug_round46_topology_v2_corrected_validation_v4_20260731T1620_run
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
VENDOR=${BASE}/vendor/py311
RUNNER=${PACKAGE}/run_tpch_round29_diagnostic_exact_plan_regime_batch.py
SELECTION=${PACKAGE}/top5.json
GATE=${PACKAGE}/gate.json
CONFIG=${PACKAGE}/config.json
RESUME=${BASE}/formal10_exact_plan_regime_round28_feedback_v1_20260729T2055_run/formal_state.json
WORKLOAD=${PACKAGE}/run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh
BASELINE=${BASE}/candidate_measurements/formal_point_20260729T050311Z_3000/summary.json
COUNTER=${BASE}/run/attempt48_resource_correction_22c1730m16r20w36w_rolling_v2.accepted_shrink.counter.txt
CONTAINER=pg5_tpch_ap_slot_node01_20260726
DATABASE=template_fullgrid_tpch_pg5

SELECTION_SHA=cc2fadcfc29a327304e65d9fa07e594d441e0e174304ee6cd9f5cb8b25157238
GATE_SHA=02d19baf6ad8ef9bce7432243ed1ffcb97ed7b402e2fe7c4199243260c109be1
CONFIG_SHA=0b6be9e78681028b730682e87bf01a76306169336e921c60b03bd7c0dfd69b92
RUNNER_SHA=7ab3449c70845732f19f8beeb0a714b6cc57fc3c065cc5c657215b31c93b507c
WORKLOAD_SHA=25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008
BASELINE_SHA=50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67
RESUME_SHA=335308f7c19be9bf958b50041f61a8b33fbace17c5ec546942ad35d687f7ecb4

test ! -e "${OUTPUT}"
test "$(tr -d '[:space:]' < "${COUNTER}")" = "1"
test "$(sha256sum "${SELECTION}" | awk '{print $1}')" = "${SELECTION_SHA}"
test "$(sha256sum "${GATE}" | awk '{print $1}')" = "${GATE_SHA}"
test "$(sha256sum "${CONFIG}" | awk '{print $1}')" = "${CONFIG_SHA}"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = "${RUNNER_SHA}"
test "$(sha256sum "${WORKLOAD}" | awk '{print $1}')" = "${WORKLOAD_SHA}"
test "$(sha256sum "${BASELINE}" | awk '{print $1}')" = "${BASELINE_SHA}"
test "$(sha256sum "${RESUME}" | awk '{print $1}')" = "${RESUME_SHA}"

jq -e '
    .selection_contract ==
        "exact_structural_plan_regime_trusted3_unknown_probe2_top5_diagnostic_v1" and
    .diagnostic_only == true and
    .formal_continuation == false and
    .maximum_real_replay_invocations == 5 and
    .accepted_shrink_target == 10 and
    .qps_used_for_inner_scoring == false and
    [.top5[].selection_role] == ["trusted", "trusted", "trusted", "probe", "probe"] and
    ([.top5[].three_level_fingerprint.id] | unique | length) == 5 and
    all(
        .top5[];
        .boundary_corner == false and
        .three_level_fingerprint.valid == true and
        has("feature_ratio") and
        has("predicted_resource") and
        has("utilization") and
        has("dScore/dU") and
        has("T") and
        has("confidence") and
        has("g_j") and
        has("eta") and
        has("delta_param") and
        has("candidate_score")
    )
' "${SELECTION}" >/dev/null

jq -e '
    .successful_real_replay_count == 5 and
    .technical_failure_count == 0 and
    .baseline_params_restored == true and
    .diagnostic_only == true and
    .formal_state_unchanged == true
' "${GATE}" >/dev/null

jq -e '
    .schema_version == "tpch_exact_plan_regime_formal_state_v1" and
    .status == "complete" and
    .baseline_restored == true and
    .accepted_count == 1 and
    .technical_failure_count == 0 and
    .incumbent_score == 3.8986963828608943 and
    .incumbent_resource == {
        "cpu": 0.4625159417999354,
        "io": 15.998865538156531,
        "memory": 1.0863863203260633
    }
' "${RESUME}" >/dev/null

test -z "$(
    ps -eo comm,args |
        awk '$1 ~ /python/ && $0 ~ /run_tpch_round29_diagnostic_exact_plan_regime_batch/ {print}'
)"
test "$(
    docker inspect -f \
        '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.CpusetCpus}}' \
        "${CONTAINER}"
)" = "true 22000000000 1730000000 1730000000 0-15,36-51"

EXPECTED_SETTINGS=$'cpu_index_tuple_cost=0.005\ncpu_operator_cost=0.0025\ncpu_tuple_cost=0.01\nparallel_setup_cost=1000\nparallel_tuple_cost=0.1\nrandom_page_cost=4\nseq_page_cost=1'
ACTUAL_SETTINGS="$(
    docker exec "${CONTAINER}" psql -X -At -p 64304 -U postgres \
        -d "${DATABASE}" \
        -c "SELECT name || '=' || setting FROM pg_settings WHERE name IN ('cpu_index_tuple_cost','cpu_operator_cost','cpu_tuple_cost','parallel_setup_cost','parallel_tuple_cost','random_page_cost','seq_page_cost') ORDER BY name;"
)"
test "${ACTUAL_SETTINGS}" = "${EXPECTED_SETTINGS}"

mkdir -p "${OUTPUT}"
nohup env -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME \
    PYTHONPATH="${PACKAGE}:${VENDOR}:${BASE}/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages" \
    PYTHONNOUSERSITE=1 \
    LD_PRELOAD=/usr/lib64/libstdc++.so.6 \
    PGPASSWORD=postgres PGHOST=127.0.0.1 PGPORT=63301 PGUSER=postgres \
    "${PYTHON}" -u "${RUNNER}" \
        --diagnostic-only \
        --selection "${SELECTION}" \
        --selection-sha256 "${SELECTION_SHA}" \
        --gate-summary "${GATE}" \
        --gate-summary-sha256 "${GATE_SHA}" \
        --baseline-summary "${BASELINE}" \
        --baseline-summary-sha256 "${BASELINE_SHA}" \
        --config "${CONFIG}" \
        --config-sha256 "${CONFIG_SHA}" \
        --workload "${WORKLOAD}" \
        --workload-sha256 "${WORKLOAD_SHA}" \
        --accepted-counter "${COUNTER}" \
        --resume-state "${RESUME}" \
        --resume-state-sha256 "${RESUME_SHA}" \
        --selection-start-rank 1 \
        --maximum-real-replay-invocations 5 \
        --output "${OUTPUT}" \
        --container "${CONTAINER}" \
        --database "${DATABASE}" \
        --timeout-seconds 900 >"${OUTPUT}/driver.log" 2>&1 &

pid=$!
printf '%s\n' "${pid}" >"${OUTPUT}/driver.pid"
sleep 3
kill -0 "${pid}"
printf 'PID=%s\n' "${pid}"
