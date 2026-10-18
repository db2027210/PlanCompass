#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
LIVE="$BASE/config/live.json"
WORKLOAD="$BASE/code/preflight/run_tpch_weighted_point_attempt48_cpu32_cachefixed.sh"
OPTIMIZER="$BASE/code/parameter_optimizer.py"
AUTH_SOURCE="$BASE/gradient_debug_round17_bound_generic_custom_v1_20260729/run_controller.sh"
CONTAINER=pg5_tpch_ap_slot_node01_20260726
LOG="$BASE/logs/attempt48_optimizer_after_workload_binding_fix.log"
STATE="$BASE/run/attempt48_formal10_start.state.json"
AUDIT="$BASE/run/attempt48_formal10_start.audit.json"
VENDOR="$BASE/vendor/py311"
SYSTEM_LIBSTDCPP=/usr/lib64/libstdc++.so.6

EXPECTED_LIVE_SHA=23da90cdb9123c76a0bbf2a39a8899b43de955ed4cdf63d7b4abb098e060b380
EXPECTED_WORKLOAD_SHA=6ff25f942687cfd19a93a3c7b52d701351910d18a9de6aa8dc18900a6adb0a51
EXPECTED_AUTH_SOURCE_SHA=3216c95823218395b50ba23e8d7c82c83f0e83db3027b4b683c67ce2c281bc2d

fail() { printf 'ERROR %s\n' "$*" >&2; exit 1; }
sha() { sha256sum "$1" | awk '{print $1}'; }

[[ -x "$PYTHON" && -f "$OPTIMIZER" && -x "$WORKLOAD" ]] \
    || fail "required executable is missing"
[[ -d "$VENDOR" ]] || fail "attempt-scoped Python vendor directory is missing"
[[ -r "$SYSTEM_LIBSTDCPP" ]] || fail "system libstdc++ compatibility library is missing"
[[ "$(sha "$LIVE")" == "$EXPECTED_LIVE_SHA" ]] || fail "live config SHA mismatch"
[[ "$(sha "$WORKLOAD")" == "$EXPECTED_WORKLOAD_SHA" ]] || fail "workload SHA mismatch"
[[ "$(sha "$AUTH_SOURCE")" == "$EXPECTED_AUTH_SOURCE_SHA" ]] \
    || fail "credential source SHA mismatch"
[[ "$(docker inspect -f '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' "$CONTAINER")" \
    == "true 22000000000 1730000000 1730000000" ]] || fail "active resource state mismatch"
[[ "$("$PYTHON" - "$LIVE" "$WORKLOAD" <<'PY'
import json
import sys

cfg = json.load(open(sys.argv[1], encoding="utf-8"))
assert cfg["benchmark"]["workload_cmd"] == sys.argv[2]
assert cfg["metrics"]["docker_runtime"]["container"] == "pg5_tpch_ap_slot_node01_20260726"
assert cfg["post_benchmark_cleanup"]["db_container"] == "pg5_tpch_ap_slot_node01_20260726"
assert cfg["_tpch_runtime_resource_contract"]["cpu_quota_cores"] == 22
assert cfg["_tpch_runtime_resource_contract"]["memory_limit_bytes"] == 1_730_000_000
assert cfg["_tpch_runtime_resource_contract"]["workers"] == 36
assert cfg["capability"] == {
    "cpu": 22,
    "memory": 1_730_000_000 / (1024 ** 3),
    "io": 36,
}
assert cfg["_physical_resource_contract"]["cpu_quota_cores"] == 22
assert cfg["_physical_resource_contract"]["memory_bytes"] == 1_730_000_000
assert cfg["_physical_resource_contract"]["io_device"] == "/dev/nvme1n1"
stable = cfg["_tpch_formal_stable_r0_contract"]
assert stable["whole_process_startup_average_forbidden_for_baseline_gate"] is True
assert stable["profile_construction_gate_required"] is True
assert stable["formal_resource_gate_required"] is True
assert cfg["formal_outer_protocol"]["accepted_shrink_target"] == 10
assert cfg["formal_outer_protocol"]["formal_search_accepted_target"] == 10
assert cfg["formal_outer_protocol"]["formal_search_max_replay_top_k"] == 5
assert cfg["_algorithm_c_ratio_resource_only"]["inner_score"]["qps"] == "excluded"
print("PASS")
PY
)" == PASS ]] || fail "formal config contract check failed"
if pgrep -af '[p]arameter_optimizer.py' | grep -F "$BASE" >/dev/null; then
    fail "attempt48 optimizer already running"
fi

PGPASSWORD_VALUE=$(sed -n 's/^export PGPASSWORD=//p' "$AUTH_SOURCE" | head -1)
[[ -n "$PGPASSWORD_VALUE" ]] || fail "trusted credential source has no PGPASSWORD"
case "$PGPASSWORD_VALUE" in
    *[!A-Za-z0-9_.-]*) fail "trusted PGPASSWORD contains unexpected characters" ;;
esac
export PGPASSWORD="$PGPASSWORD_VALUE"

read -r PGHOST_VALUE PGPORT_VALUE PGUSER_VALUE PGDATABASE_VALUE < <(
    "$PYTHON" - "$LIVE" <<'PY'
import json
import sys

pg = json.load(open(sys.argv[1], encoding="utf-8"))["postgres"]
print(pg["host"], pg["port"], pg["user"], pg["dbname"])
PY
)
env -u LD_LIBRARY_PATH psql -w \
    -h "$PGHOST_VALUE" -p "$PGPORT_VALUE" -U "$PGUSER_VALUE" -d "$PGDATABASE_VALUE" \
    -Atqc "SELECT 1" | grep -Fx 1 >/dev/null || fail "read-only database probe failed"

SHIM="$BASE/run/python311-shim"
mkdir -p "$SHIM"
ln -sfn "$PYTHON" "$SHIM/python3"
ln -sfn "$PYTHON" "$SHIM/python"
export PATH="$SHIM:$(dirname "$PYTHON"):$PATH"
export PYTHON="$PYTHON"
export PYTHON3="$PYTHON"
export PYTHONPATH="$VENDOR:$BASE/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages"
export PYTHONNOUSERSITE=1
export LD_PRELOAD="$SYSTEM_LIBSTDCPP"
unset PYTHONHOME
"$PYTHON" -c \
    'import joblib, numpy, pandas, pyarrow, scipy, sklearn; print("TPCH_ML_IMPORTS_OK")' \
    | grep -Fx TPCH_ML_IMPORTS_OK >/dev/null \
    || fail "attempt-scoped ML dependency smoke test failed"

env -u LD_LIBRARY_PATH -u CANARY_MAX_OUTER_ROUNDS -u BONECT_FORCE_CANDIDATE_REPLAY_TOP_K \
    OPTIMIZER_CONFIG="$LIVE" TPC_H_CONFIG="$LIVE" CONFIG_PATH="$LIVE" LIVE_CONFIG="$LIVE" \
    nohup "$PYTHON" -u "$OPTIMIZER" >>"$LOG" 2>&1 &
PID=$!
printf '%s\n' "$PID" >"$BASE/run/attempt48_optimizer.pid"
sleep 15
kill -0 "$PID" 2>/dev/null || fail "optimizer exited during corrected startup"
if ! pgrep -af '[r]un_tpch_weighted_point_attempt48_cpu32_cachefixed.sh' \
    | grep -F "$BASE" >/dev/null \
    && ! pgrep -af '[r]un_pg_weighted_sql_baseline' | grep -F "$BASE" >/dev/null; then
    fail "corrected formal workload child is not running"
fi

"$PYTHON" - "$STATE" "$AUDIT" "$PID" <<'PY'
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

state_path, audit_path, pid = sys.argv[1:]
now = datetime.now(timezone.utc).isoformat()

def atomic(path, payload):
    fd, temporary = tempfile.mkstemp(
        prefix=".tpch-formal-start.", dir=os.path.dirname(path), text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

atomic(state_path, {
    "schema_version": "tpch_formal10_start_state_v1",
    "status": "running",
    "stage": "formal_baseline_workload_running",
    "optimizer_pid": int(pid),
    "formal_accepted_count": 0,
    "accepted_shrink_target": 10,
    "updated_at": now,
})
atomic(audit_path, {
    "schema_version": "tpch_formal10_start_audit_v1",
    "status": "formal_started",
    "optimizer_pid": int(pid),
    "corrected_workload_child_verified": True,
    "live_config_sha256": "23da90cdb9123c76a0bbf2a39a8899b43de955ed4cdf63d7b4abb098e060b380",
    "workload_sha256": "6ff25f942687cfd19a93a3c7b52d701351910d18a9de6aa8dc18900a6adb0a51",
    "formal_accepted_added": 0,
    "accepted_shrink_target": 10,
    "started_at": now,
})
PY

printf 'TPCH_FORMAL_STARTED optimizer_pid=%s workload_sha=%s\n' \
    "$PID" "$EXPECTED_WORKLOAD_SHA"
