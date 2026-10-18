#!/usr/bin/env bash
set -euo pipefail

ROOT=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
SOURCE=${ROOT}/gradient_debug_round17_bound_generic_custom_v1_20260729
ROUND=${ROOT}/gradient_debug_round24_accepted1_candidates_v2_20260729T1706
CODE=${SOURCE}/code
BASE_CODE=${ROOT}/code
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
PSQL=/usr/bin/psql
ACCEPTED_COUNTER=${ROOT}/run/attempt48_resource_correction_22c1730m16r20w36w_rolling_v2.accepted_shrink.counter.txt
OPTIMIZER_SHA=9a6b67e4ec1433e8c577a9bf18b5728610f0af24967746a4e37838d18e17a200
PLAN_SIGNATURE_SHA=9bd2bb9e5e195abb03702c305c1d088e64496c82e7292b32cb08f628497590d4
optimizer_pid=

restore_incumbent() {
    local failed=0 assignment verify_output
    local assignments=(
        "seq_page_cost=1"
        "random_page_cost=4"
        "cpu_tuple_cost=0.00571428571428572"
        "cpu_index_tuple_cost=0.010571428571428572"
        "cpu_operator_cost=0.0025"
        "parallel_setup_cost=0"
        "parallel_tuple_cost=0.001"
    )
    for assignment in "${assignments[@]}"; do
        PGPASSWORD=postgres PGHOST=127.0.0.1 PGPORT=63301 PGUSER=postgres \
            "${PSQL}" -d template_fullgrid_tpch_pg5 -v ON_ERROR_STOP=1 \
            -c "ALTER SYSTEM SET ${assignment%%=*} = '${assignment#*=}';" \
            >/dev/null 2>&1 || failed=1
    done
    PGPASSWORD=postgres PGHOST=127.0.0.1 PGPORT=63301 PGUSER=postgres \
        "${PSQL}" -d template_fullgrid_tpch_pg5 -v ON_ERROR_STOP=1 \
        -Atc "SELECT pg_reload_conf();" >/dev/null 2>&1 || failed=1
    verify_output="$(
        PGPASSWORD=postgres PGHOST=127.0.0.1 PGPORT=63301 PGUSER=postgres \
            "${PSQL}" -d template_fullgrid_tpch_pg5 -Atc \
            "SELECT name||'='||setting FROM pg_settings WHERE name IN ('seq_page_cost','random_page_cost','cpu_tuple_cost','cpu_index_tuple_cost','cpu_operator_cost','parallel_setup_cost','parallel_tuple_cost') ORDER BY name;" \
            2>&1
    )" || failed=1
    printf '%s\n' "${verify_output}" >"${ROUND}/baseline_restore_readback.txt"
    return "${failed}"
}

finalize() {
    local optimizer_rc=$? restore_rc=0
    trap - EXIT INT TERM
    restore_incumbent || restore_rc=$?
    if [[ "${optimizer_rc}" -eq 0 && "${restore_rc}" -ne 0 ]]; then
        optimizer_rc=97
    fi
    printf '%s\n' "${optimizer_rc}" >"${ROUND}/controller_exit_code.txt"
    exit "${optimizer_rc}"
}

terminate_controller() {
    local signal_rc="$1"
    if [[ -n "${optimizer_pid}" ]] && kill -0 "${optimizer_pid}" 2>/dev/null; then
        kill -TERM "${optimizer_pid}" 2>/dev/null || true
        wait "${optimizer_pid}" 2>/dev/null || true
    fi
    exit "${signal_rc}"
}

trap finalize EXIT
trap 'terminate_controller 130' INT
trap 'terminate_controller 143' TERM

test "$(cat "${ACCEPTED_COUNTER}")" = "1"
test "$(sha256sum "${CODE}/parameter_optimizer.py" | awk '{print $1}')" = \
    "${OPTIMIZER_SHA}"
test "$(sha256sum "${ROUND}/plan_signature_calibration.json" | awk '{print $1}')" = \
    "${PLAN_SIGNATURE_SHA}"
test "$(sha256sum "${ROUND}/config.json" | awk '{print $1}')" = \
    "$("${PYTHON}" -c 'import json,sys; print(json.load(open(sys.argv[1]))["config_sha256"])' "${ROUND}/manifest.json")"
if pgrep -af '[p]arameter_optimizer.py' | grep -F "${ROOT}" >/dev/null; then
    echo TPCH_OPTIMIZER_ALREADY_ACTIVE >&2
    exit 73
fi

unset LD_LIBRARY_PATH
unset PYTHONHOME
export PYTHONNOUSERSITE=1
export PYTHONPATH="${CODE}:${BASE_CODE}:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages"
export PGPASSWORD=postgres PGHOST=127.0.0.1 PGPORT=63301 PGUSER=postgres
export OPTIMIZER_CONFIG="${ROUND}/config.json"
export TPC_H_CONFIG="${ROUND}/config.json"
export CONFIG_PATH="${ROUND}/config.json"
export LIVE_CONFIG="${ROUND}/config.json"
export BONECT_OPTIMIZER_LOCK_PATH=/tmp/resource_adaptive_tpch_attempt48_gradient_control.lock
export CANARY_MAX_OUTER_ROUNDS=1
export BONECT_FORCE_CANDIDATE_REPLAY_TOP_K=5
export RESOURCE_ADAPTIVE_GENERATE_CANDIDATES_ONLY=1

cd "${BASE_CODE}"
"${PYTHON}" -u "${CODE}/parameter_optimizer.py" &
optimizer_pid=$!
printf '%s\n' "${optimizer_pid}" >"${ROUND}/optimizer.pid"
wait "${optimizer_pid}"
optimizer_rc=$?
optimizer_pid=
exit "${optimizer_rc}"
