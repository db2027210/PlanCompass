#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
ROUND="$BASE/gradient_debug_round21_qps_outer_only_exact_regime_v1_20260729"
PREVIOUS="$BASE/formal10_exact_plan_regime_round22_py311isolated_20260729T1515"
FORMAL="$BASE/formal10_exact_plan_regime_round23_candidate_v2_20260729T1540"
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
SELECTION="$ROUND/tpch_round21_corrected_top5.json"
GATE="$ROUND/round20_corrected_entry_gate.json"
BASELINE="$BASE/candidate_measurements/formal_point_20260729T050311Z_3000/summary.json"
CONFIG="$BASE/config/live.json"
WORKLOAD="$ROUND/code/run_tpch_formal_workload_candidate_v2.sh"
COUNTER="$BASE/run/attempt48_resource_correction_22c1730m16r20w36w_rolling_v2.accepted_shrink.counter.txt"
RUNNER="$ROUND/code/run_tpch_formal_exact_plan_regime_batch.py"
CONTAINER=pg5_tpch_ap_slot_node01_20260726
DATABASE=template_fullgrid_tpch_pg5
VENDOR="$BASE/vendor/py311"
SYSTEM_LIBSTDCPP=/usr/lib64/libstdc++.so.6

EXPECTED_RUNNER_SHA=cc5b6d151c8a325d94f49e12561995f0f0884190816234cb6db186d57f5c1795
EXPECTED_SELECTION_SHA=28db8c441c45f2f76345da80ce95e56b4efcc92ce36fe571b351fed42e990ad7
EXPECTED_GATE_SHA=7eeab3878604d568f0e6eb715b9b805145174e9245dfde9db6d7d0c0246dea91
EXPECTED_BASELINE_SHA=50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67
EXPECTED_CONFIG_SHA=23da90cdb9123c76a0bbf2a39a8899b43de955ed4cdf63d7b4abb098e060b380
EXPECTED_WORKLOAD_SHA=625e8458bcfccb448bcc5e7d6bc5a0416c57f3186e3012848aa85d483de30b06
EXPECTED_PREVIOUS_SUMMARY_SHA=41ba22f26da92aeb055e8d4b34461f2a3708701a5e169db5e11956fd497129d9
EXPECTED_PREVIOUS_STATE_SHA=df7337d349485ad1d7a9a62976020c4bba8e50aa57861ae5f2ca1c4263bdff71

fail() {
    printf 'ERROR %s\n' "$*" >&2
    exit 1
}

sha() {
    sha256sum "$1" | awk '{print $1}'
}

for binding in \
    "$RUNNER:$EXPECTED_RUNNER_SHA" \
    "$SELECTION:$EXPECTED_SELECTION_SHA" \
    "$GATE:$EXPECTED_GATE_SHA" \
    "$BASELINE:$EXPECTED_BASELINE_SHA" \
    "$CONFIG:$EXPECTED_CONFIG_SHA" \
    "$WORKLOAD:$EXPECTED_WORKLOAD_SHA" \
    "$PREVIOUS/summary.json:$EXPECTED_PREVIOUS_SUMMARY_SHA" \
    "$PREVIOUS/formal_state.json:$EXPECTED_PREVIOUS_STATE_SHA"; do
    path=${binding%%:*}
    expected=${binding##*:}
    [[ -f "$path" ]] || fail "missing file: $path"
    [[ "$(sha "$path")" == "$expected" ]] || fail "SHA mismatch: $path"
done

jq -e '
    .status == "complete"
    and .physical_call_count == 2
    and .technical_failure_count == 2
    and .valid_replay_count == 0
    and .accepted_count == 0
    and .baseline_restored == true
' "$PREVIOUS/formal_state.json" >/dev/null \
    || fail "previous technical-failure state mismatch"
[[ -x "$PYTHON" && -d "$VENDOR" && -r "$SYSTEM_LIBSTDCPP" ]] \
    || fail "isolated Python runtime support is missing"
[[ -f "$COUNTER" && "$(<"$COUNTER")" == 0 ]] \
    || fail "accepted counter is not zero"
[[ ! -e "$FORMAL" ]] || fail "formal output already exists"
if pgrep -af '^.*/python(3(\.11)?)? .*run_tpch_formal_exact_plan_regime_batch\.py ' \
    | grep -F "$BASE" >/dev/null; then
    fail "TPC-H formal batch is already running"
fi
if pgrep -af '^(/usr/bin/)?(bash|sh) .*/run_tpch_formal_workload_candidate_v2\.sh( |$)' \
    | grep -F "$BASE" >/dev/null; then
    fail "TPC-H workload is already running"
fi
[[ "$(docker inspect -f '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' "$CONTAINER")" \
    == "true 22000000000 1730000000 1730000000" ]] \
    || fail "TPC-H container resource state mismatch"

EXPECTED_SETTINGS=$'cpu_index_tuple_cost=0.005\ncpu_operator_cost=0.0025\ncpu_tuple_cost=0.01\nparallel_setup_cost=1000\nparallel_tuple_cost=0.1\nrandom_page_cost=4\nseq_page_cost=1'
ACTUAL_SETTINGS=$(docker exec "$CONTAINER" psql -X -At \
    -p 64304 -U postgres -d "$DATABASE" \
    -c "SELECT name || '=' || setting FROM pg_settings WHERE name IN ('cpu_index_tuple_cost','cpu_operator_cost','cpu_tuple_cost','parallel_setup_cost','parallel_tuple_cost','random_page_cost','seq_page_cost') ORDER BY name;")
[[ "$ACTUAL_SETTINGS" == "$EXPECTED_SETTINGS" ]] \
    || fail "baseline PostgreSQL settings are not restored"

export PYTHONPATH="$VENDOR:$BASE/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages"
export PYTHONNOUSERSITE=1
export LD_PRELOAD="$SYSTEM_LIBSTDCPP"
unset PYTHONHOME
unset LD_LIBRARY_PATH
"$PYTHON" -c '
import dataclasses
import formal_measurement
assert "python3.11" in dataclasses.__file__, dataclasses.__file__
print("TPCH_PY311_ISOLATION_OK")
' | grep -Fx TPCH_PY311_ISOLATION_OK >/dev/null \
    || fail "Python 3.11 isolation preflight failed"

mkdir -p "$FORMAL"
LOG="$FORMAL/driver.log"
setsid "$PYTHON" -u "$RUNNER" \
    --selection "$SELECTION" \
    --selection-sha256 "$EXPECTED_SELECTION_SHA" \
    --gate-summary "$GATE" \
    --gate-summary-sha256 "$EXPECTED_GATE_SHA" \
    --baseline-summary "$BASELINE" \
    --baseline-summary-sha256 "$EXPECTED_BASELINE_SHA" \
    --config "$CONFIG" \
    --config-sha256 "$EXPECTED_CONFIG_SHA" \
    --workload "$WORKLOAD" \
    --workload-sha256 "$EXPECTED_WORKLOAD_SHA" \
    --accepted-counter "$COUNTER" \
    --output "$FORMAL" \
    --container "$CONTAINER" \
    --database "$DATABASE" \
    --timeout-seconds 900 \
    >>"$LOG" 2>&1 < /dev/null &
PID=$!
printf '%s\n' "$PID" >"$FORMAL/driver.pid"
sleep 5
kill -0 "$PID" 2>/dev/null || {
    tail -80 "$LOG" >&2
    fail "formal runner exited during launch"
}
jq -e '
    .status == "running"
    and .physical_call_count == 1
    and .technical_failure_count == 0
    and .accepted_count == 0
' "$FORMAL/formal_state.json" >/dev/null \
    || fail "formal runner startup state mismatch"
printf 'TPCH_ROUND23_FORMAL_STARTED pid=%s output=%s\n' "$PID" "$FORMAL"
