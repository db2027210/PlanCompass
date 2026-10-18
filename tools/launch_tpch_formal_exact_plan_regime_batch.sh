#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
CONTROLLER="$BASE/code/run_tpch_formal_exact_plan_regime_batch.py"
SELECTION="$BASE/gradient_debug_round20_exact_plan_regime_v2_20260729/tpch_round20_corrected_top5.json"
GATE_SUMMARY="$BASE/gradient_debug_round20_exact_plan_regime_v2_20260729/top5_serial_real_replay_round20_v1_retry2/summary.json"
BASELINE_SUMMARY="$BASE/candidate_measurements/formal_point_20260729T050311Z_3000/summary.json"
CONFIG="$BASE/config/live.json"
WORKLOAD="$BASE/code/preflight/run_tpch_weighted_candidate_attempt48.sh"
ACCEPTED_COUNTER="$BASE/run/attempt48_resource_correction_22c1730m16r20w36w_rolling_v2.accepted_shrink.counter.txt"
AUTH_SOURCE="$BASE/gradient_debug_round17_bound_generic_custom_v1_20260729/run_controller.sh"
OUTPUT="$BASE/formal10_exact_plan_regime_v1_20260729T1400_retry2"
LOG="$OUTPUT/driver.log"
PID_FILE="$OUTPUT/driver.pid"
CONTAINER=pg5_tpch_ap_slot_node01_20260726
DATABASE=template_fullgrid_tpch_pg5
VENDOR="$BASE/vendor/py311"
SYSTEM_LIBSTDCPP=/usr/lib64/libstdc++.so.6

EXPECTED_CONTROLLER_SHA=5698ba8948b30512492b8480e4244d08d88af3ecb62ba73501c81bcc10731c55
EXPECTED_SELECTION_SHA=84d9f671c673626ea5699605110df044dd1c7dc91d176c28ac00be0ddd9e441f
EXPECTED_GATE_SUMMARY_SHA=b6da900c2885b739d20a9a7b340e2b2f4ed47d5f982baccc45b0b98c4840b922
EXPECTED_BASELINE_SUMMARY_SHA=50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67
EXPECTED_CONFIG_SHA=23da90cdb9123c76a0bbf2a39a8899b43de955ed4cdf63d7b4abb098e060b380
EXPECTED_WORKLOAD_SHA=19595534dccb0fa1fb6d6b2a5de66445516095dcd1511d772d1787e58b7c0c12
EXPECTED_AUTH_SOURCE_SHA=3216c95823218395b50ba23e8d7c82c83f0e83db3027b4b683c67ce2c281bc2d

fail() {
    printf 'ERROR %s\n' "$*" >&2
    exit 1
}

sha() {
    sha256sum "$1" | awk '{print $1}'
}

[[ -x "$PYTHON" && -f "$CONTROLLER" && -x "$WORKLOAD" ]] \
    || fail "required executable is missing"
[[ -d "$VENDOR" && -r "$SYSTEM_LIBSTDCPP" ]] \
    || fail "attempt-scoped Python runtime support is missing"
[[ "$(sha "$CONTROLLER")" == "$EXPECTED_CONTROLLER_SHA" ]] \
    || fail "controller SHA mismatch"
[[ "$(sha "$SELECTION")" == "$EXPECTED_SELECTION_SHA" ]] \
    || fail "selection SHA mismatch"
[[ "$(sha "$GATE_SUMMARY")" == "$EXPECTED_GATE_SUMMARY_SHA" ]] \
    || fail "gate summary SHA mismatch"
[[ "$(sha "$BASELINE_SUMMARY")" == "$EXPECTED_BASELINE_SUMMARY_SHA" ]] \
    || fail "baseline summary SHA mismatch"
[[ "$(sha "$CONFIG")" == "$EXPECTED_CONFIG_SHA" ]] \
    || fail "config SHA mismatch"
[[ "$(sha "$WORKLOAD")" == "$EXPECTED_WORKLOAD_SHA" ]] \
    || fail "workload SHA mismatch"
[[ "$(sha "$AUTH_SOURCE")" == "$EXPECTED_AUTH_SOURCE_SHA" ]] \
    || fail "credential source SHA mismatch"
[[ "$(tr -d '\n' <"$ACCEPTED_COUNTER")" == 0 ]] \
    || fail "formal accepted counter is not zero"
[[ ! -e "$OUTPUT" ]] || fail "formal output already exists"
if pgrep -af '[p]arameter_optimizer.py' | grep -F "$BASE" >/dev/null; then
    fail "attempt48 optimizer already running"
fi
if pgrep -af '[r]un_tpch_formal_exact_plan_regime_batch.py' \
    | grep -F "$BASE" >/dev/null; then
    fail "formal exact-plan-regime controller already running"
fi
[[ "$(docker inspect -f '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' "$CONTAINER")" \
    == "true 22000000000 1730000000 1730000000" ]] \
    || fail "active PostgreSQL resource state mismatch"
EXPECTED_SETTINGS=$'cpu_index_tuple_cost=0.005\ncpu_operator_cost=0.0025\ncpu_tuple_cost=0.01\nparallel_setup_cost=1000\nparallel_tuple_cost=0.1\nrandom_page_cost=4\nseq_page_cost=1'
ACTUAL_SETTINGS=$(docker exec "$CONTAINER" psql -X -At \
    -p 64304 -U postgres -d "$DATABASE" \
    -c "SELECT name || '=' || setting FROM pg_settings WHERE name IN ('cpu_index_tuple_cost','cpu_operator_cost','cpu_tuple_cost','parallel_setup_cost','parallel_tuple_cost','random_page_cost','seq_page_cost') ORDER BY name;")
[[ "$ACTUAL_SETTINGS" == "$EXPECTED_SETTINGS" ]] \
    || fail "baseline PostgreSQL settings are not restored"

PGPASSWORD_VALUE=$(sed -n 's/^export PGPASSWORD=//p' "$AUTH_SOURCE" | head -1)
[[ -n "$PGPASSWORD_VALUE" ]] || fail "trusted credential source has no PGPASSWORD"
case "$PGPASSWORD_VALUE" in
    *[!A-Za-z0-9_.-]*) fail "trusted PGPASSWORD contains unexpected characters" ;;
esac
export PGPASSWORD="$PGPASSWORD_VALUE"
export PYTHONPATH="$VENDOR:$BASE/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages"
export PYTHONNOUSERSITE=1
export LD_PRELOAD="$SYSTEM_LIBSTDCPP"
unset PYTHONHOME
"$PYTHON" -c \
    'import joblib, numpy, pandas, pyarrow, scipy, sklearn; print("TPCH_ML_IMPORTS_OK")' \
    | grep -Fx TPCH_ML_IMPORTS_OK >/dev/null \
    || fail "attempt-scoped ML dependency smoke test failed"

mkdir -p "$OUTPUT"
env -u LD_LIBRARY_PATH nohup "$PYTHON" -u "$CONTROLLER" \
    --selection "$SELECTION" \
    --selection-sha256 "$EXPECTED_SELECTION_SHA" \
    --gate-summary "$GATE_SUMMARY" \
    --gate-summary-sha256 "$EXPECTED_GATE_SUMMARY_SHA" \
    --baseline-summary "$BASELINE_SUMMARY" \
    --baseline-summary-sha256 "$EXPECTED_BASELINE_SUMMARY_SHA" \
    --config "$CONFIG" \
    --config-sha256 "$EXPECTED_CONFIG_SHA" \
    --workload "$WORKLOAD" \
    --workload-sha256 "$EXPECTED_WORKLOAD_SHA" \
    --accepted-counter "$ACCEPTED_COUNTER" \
    --output "$OUTPUT" \
    --container "$CONTAINER" \
    --database "$DATABASE" \
    --timeout-seconds 900 \
    >"$LOG" 2>&1 &
PID=$!
printf '%s\n' "$PID" >"$PID_FILE"
sleep 15
kill -0 "$PID" 2>/dev/null || fail "formal controller exited during startup"
"$PYTHON" - "$OUTPUT/formal_state.json" "$PID" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["schema_version"] == "tpch_exact_plan_regime_formal_state_v1"
assert state["status"] == "running"
assert state["pid"] == int(sys.argv[2])
assert state["accepted_shrink_target"] == 10
assert state["accepted_count"] == 0
assert state["physical_call_count"] == 1
assert state["qps_used_for_inner_scoring"] is False
PY

printf 'TPCH_FORMAL_EXACT_PLAN_REGIME_STARTED pid=%s output=%s\n' "$PID" "$OUTPUT"
