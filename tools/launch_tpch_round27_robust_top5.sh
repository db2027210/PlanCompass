#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
CODE_SOURCE=${BASE}/gradient_debug_round19_exact_plan_regime_v1_20260729/code
ROOT=${BASE}/gradient_debug_round27_robust_empirical_regime_v6_20260729T1854
TOP5=${ROOT}/top5.json
REPLAY=${ROOT}/top5_serial_real_replay_robust
RUNNER=${ROOT}/run_tpch_gradient_top5_real_replay.py
PSQL=/usr/bin/psql
EXPECTED_TOP5_SHA=8865741a7f2a7840b4a2feef97a355bee9e1fb7b4aab71c73e6558e8faa4e531
EXPECTED_RUNNER_SHA=b5391d9d99bb2318aa74cc2718576f8c3fe75e6801ee9fe156b07cd0e4c3d731
EXPECTED_CONTRACT_SHA=9c5ce54e4a18d139357370419aea54957c9a5acc2d164b81fafe5e0b5fd5e413

restore_accepted_incumbent() {
    local failed=0 assignment
    for assignment in \
        seq_page_cost=1 \
        random_page_cost=4 \
        cpu_tuple_cost=0.00571428571428572 \
        cpu_index_tuple_cost=0.010571428571428572 \
        cpu_operator_cost=0.0025 \
        parallel_setup_cost=0 \
        parallel_tuple_cost=0.001
    do
        PGPASSWORD=postgres PGHOST=127.0.0.1 PGPORT=63301 PGUSER=postgres \
            "${PSQL}" -X -v ON_ERROR_STOP=1 -d template_fullgrid_tpch_pg5 \
            -c "ALTER SYSTEM SET ${assignment%%=*} = '${assignment#*=}';" \
            >/dev/null 2>&1 || failed=1
    done
    PGPASSWORD=postgres PGHOST=127.0.0.1 PGPORT=63301 PGUSER=postgres \
        "${PSQL}" -X -v ON_ERROR_STOP=1 -d template_fullgrid_tpch_pg5 \
        -Atc "SELECT pg_reload_conf();" >/dev/null 2>&1 || failed=1
    PGPASSWORD=postgres PGHOST=127.0.0.1 PGPORT=63301 PGUSER=postgres \
        "${PSQL}" -X -A -t -F = -d template_fullgrid_tpch_pg5 \
        -c "SELECT name,setting FROM pg_settings WHERE name IN ('seq_page_cost','random_page_cost','cpu_tuple_cost','cpu_index_tuple_cost','cpu_operator_cost','parallel_setup_cost','parallel_tuple_cost') ORDER BY name" \
        >"${ROOT}/accepted_baseline_restore_readback_robust.txt" 2>&1 || failed=1
    return "${failed}"
}

finalize() {
    local observed_rc=$? restore_rc=0
    trap - EXIT INT TERM
    restore_accepted_incumbent || restore_rc=$?
    if [[ "${observed_rc}" -eq 0 && "${restore_rc}" -ne 0 ]]; then
        observed_rc=97
    fi
    printf '%s\n' "${observed_rc}" >"${ROOT}/robust_controller_exit_code.txt"
    exit "${observed_rc}"
}
trap finalize EXIT

test -d "${ROOT}"
test ! -e "${REPLAY}"
test "$(sha256sum "${TOP5}" | awk '{print $1}')" = "${EXPECTED_TOP5_SHA}"
test "$(sha256sum "${CODE_SOURCE}/run_tpch_gradient_top5_real_replay.py" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${CODE_SOURCE}/tpch_round17_replay_contract.py" | awk '{print $1}')" = "${EXPECTED_CONTRACT_SHA}"
if pgrep -af '[r]un_tpch_gradient_top5_real_replay.py' | grep -F "${BASE}" >/dev/null; then
    printf 'TPCH_REPLAY_ALREADY_ACTIVE\n' >&2
    exit 73
fi

cp "${CODE_SOURCE}/run_tpch_gradient_top5_real_replay.py" "${RUNNER}"
cp "${CODE_SOURCE}/tpch_round17_replay_contract.py" "${ROOT}/"
cp "${CODE_SOURCE}/adaptive_replay_policy.py" "${ROOT}/"
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = "${EXPECTED_RUNNER_SHA}"
test "$(sha256sum "${ROOT}/tpch_round17_replay_contract.py" | awk '{print $1}')" = "${EXPECTED_CONTRACT_SHA}"

unset LD_LIBRARY_PATH
unset PYTHONHOME
export PYTHONNOUSERSITE=1
export PYTHONPATH="${ROOT}"
export ADAPTIVE_REAL_REPLAY_ENABLED=0
export TPCH_REPLAY_WORKLOAD="${BASE}/gradient_debug_round13_cache_recovery_v9_20260728T1745/tpch_gradient_debug_workload.sh"

/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11 \
    -u "${RUNNER}" "${TOP5}" "${REPLAY}" \
    >"${ROOT}/robust_driver.log" 2>&1
