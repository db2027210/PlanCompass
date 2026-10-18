#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
PACKAGE=${BASE}/gradient_debug_round29_corrected_diagnostic_replay_v1_20260729T2217_package
OUTPUT=${BASE}/gradient_debug_round29_corrected_diagnostic_replay_v1_20260729T2217_run
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
VENDOR=${BASE}/vendor/py311
RUNNER=${PACKAGE}/run_tpch_round29_diagnostic_exact_plan_regime_batch.py
VALIDATOR=${PACKAGE}/validate_tpch_round29_diagnostic_package.py
SELECTOR=${PACKAGE}/tpch_round19_plan_regime_select.py
SELECTION=${PACKAGE}/top5.json
GATE=${PACKAGE}/gate.json
CONFIG=${PACKAGE}/config.json
RESUME=${PACKAGE}/resume_state.json
POLICY=${PACKAGE}/adaptive_replay_policy.py
BASELINE=${BASE}/candidate_measurements/formal_point_20260729T050311Z_3000/summary.json
WORKLOAD=${BASE}/code/preflight/run_tpch_weighted_candidate_attempt48.sh
COUNTER=${BASE}/run/attempt48_resource_correction_22c1730m16r20w36w_rolling_v2.accepted_shrink.counter.txt
CONTAINER=pg5_tpch_ap_slot_node01_20260726
DATABASE=template_fullgrid_tpch_pg5

test ! -e "${OUTPUT}"
test "$(tr -d '[:space:]' < "${COUNTER}")" = "1"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = \
    "e5e39a6991fc611bdb2dbc6cc2853a582b54416a55d93518b68163ef96e36b14"
test "$(sha256sum "${VALIDATOR}" | awk '{print $1}')" = \
    "50f547045d2b864009624d96951a0365198e162a4b9c4282dfa9520e83543c42"
test "$(sha256sum "${SELECTOR}" | awk '{print $1}')" = \
    "4654efc4ef328172863235d3c21e04b8f0d905c5f50564a8dce128e1025ea74c"
test "$(sha256sum "${SELECTION}" | awk '{print $1}')" = \
    "c83d8597fa9f0934c64674139806781f7d4aa3660c1bce15479989038b01893e"
test "$(sha256sum "${GATE}" | awk '{print $1}')" = \
    "02d19baf6ad8ef9bce7432243ed1ffcb97ed7b402e2fe7c4199243260c109be1"
test "$(sha256sum "${CONFIG}" | awk '{print $1}')" = \
    "0b6be9e78681028b730682e87bf01a76306169336e921c60b03bd7c0dfd69b92"
test "$(sha256sum "${RESUME}" | awk '{print $1}')" = \
    "610a58fd17004c39182fa6fb95da607fa1be95e7e13f85cc88baff07c84798f5"
test "$(sha256sum "${POLICY}" | awk '{print $1}')" = \
    "c3c7a826ff1ab65c04c93510efd17b26907a79a15b3d13ff91e5ca106de94584"
test "$(sha256sum "${BASELINE}" | awk '{print $1}')" = \
    "50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67"
test "$(sha256sum "${WORKLOAD}" | awk '{print $1}')" = \
    "19595534dccb0fa1fb6d6b2a5de66445516095dcd1511d772d1787e58b7c0c12"

PYTHONPATH="${PACKAGE}" "${PYTHON}" "${VALIDATOR}" \
    --selection "${SELECTION}" \
    --resume-state "${RESUME}" \
    --config "${CONFIG}" \
    --gate "${GATE}" \
    --runner "${RUNNER}" >"${PACKAGE}/preflight_validation.json"

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
nohup env -u LD_LIBRARY_PATH \
    PYTHONPATH="${PACKAGE}:${VENDOR}:${BASE}/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages" \
    PYTHONNOUSERSITE=1 \
    LD_PRELOAD=/usr/lib64/libstdc++.so.6 \
    PGPASSWORD=postgres PGHOST=127.0.0.1 PGPORT=63301 PGUSER=postgres \
    "${PYTHON}" -u "${RUNNER}" \
        --diagnostic-only \
        --selection "${SELECTION}" \
        --selection-sha256 \
        "c83d8597fa9f0934c64674139806781f7d4aa3660c1bce15479989038b01893e" \
        --gate-summary "${GATE}" \
        --gate-summary-sha256 \
        "02d19baf6ad8ef9bce7432243ed1ffcb97ed7b402e2fe7c4199243260c109be1" \
        --baseline-summary "${BASELINE}" \
        --baseline-summary-sha256 \
        "50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67" \
        --config "${CONFIG}" \
        --config-sha256 \
        "0b6be9e78681028b730682e87bf01a76306169336e921c60b03bd7c0dfd69b92" \
        --workload "${WORKLOAD}" \
        --workload-sha256 \
        "19595534dccb0fa1fb6d6b2a5de66445516095dcd1511d772d1787e58b7c0c12" \
        --accepted-counter "${COUNTER}" \
        --resume-state "${RESUME}" \
        --resume-state-sha256 \
        "610a58fd17004c39182fa6fb95da607fa1be95e7e13f85cc88baff07c84798f5" \
        --selection-start-rank 1 \
        --maximum-real-replay-invocations 5 \
        --output "${OUTPUT}" \
        --container "${CONTAINER}" \
        --database "${DATABASE}" \
        --timeout-seconds 900 >"${OUTPUT}/driver.log" 2>&1 &

pid=$!
printf '%s\n' "${pid}" >"${OUTPUT}/driver.pid"
sleep 12
kill -0 "${pid}"
printf 'PID=%s\n' "${pid}"
