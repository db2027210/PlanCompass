#!/usr/bin/env bash
set -euo pipefail

A48=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
RUN_TAG="${TPCH_RESOURCE_RUN_TAG:-22c1710m16r20w36w_rolling_v2}"
PROPOSED="${TPCH_RESOURCE_PROPOSED:-$A48/config/live.attempt48.resource-correction-22c1710m16r20w36w.proposed.json}"
EXPECTED_SHA_FILE="${TPCH_RESOURCE_SHA_FILE:-$A48/config/live.attempt48.resource-correction-22c1710m16r20w36w.proposed.sha256}"
LIVE="$A48/config/live.json"
CONTAINER=pg5_tpch_ap_slot_node01_20260726
RUNNER="$A48/code/shell/run_pg_weighted_sql_baseline_profile_rolling_v2.py"
EXPECTED_RUNNER_SHA=091135e06946067ca7fbf16a6bf7d5ea7f4436229ce1e63993cab7ba090f443f
R0_VALIDATOR="$A48/code/validate_tpch_r0_profile.py"
OPTIMIZER="$A48/code/parameter_optimizer.py"
LOG_DIR="$A48/logs"
RUN_DIR="$A48/run"
AUTH_TOKEN="${TPCH_RESOURCE_AUTH_TOKEN:-TPCH_ATTEMPT48_RESOURCE_CHANGE_APPROVED_22C_1710000000B_16R_20W_36W_ROLLING_V2}"
EXPECTED_RUNTIME_VERSION="${TPCH_RESOURCE_RUNTIME_VERSION:-attempt48-resource-correction-adaptive-v7-r0-intersection}"
RUN_STATE="$RUN_DIR/attempt48_resource_correction_${RUN_TAG}.run_state.json"
PROGRESS_LOG="$RUN_DIR/attempt48_resource_correction_${RUN_TAG}.progress.jsonl"
ACCEPTED_COUNTER="$RUN_DIR/attempt48_resource_correction_${RUN_TAG}.accepted_shrink.counter.txt"

fail() { printf 'ERROR %s\n' "$*" >&2; exit 1; }
stamp() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '%s %s\n' "$(stamp)" "$*"; }
write_state() {
    "$PYTHON" - "$RUN_STATE" "$@" <<'PY'
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

path, status, stage, wrapper_pid, optimizer_pid, outer, replay, accepted, location, error = sys.argv[1:]

def integer(value):
    return int(value) if str(value).strip() else None

payload = {
    "schema_version": "tpch_resource_correction_owner_v1",
    "benchmark": "TPC-H",
    "machine": "node01",
    "attempt_tag": "attempt48_global_replay_cap5_20260726",
    "status": status,
    "stage": stage,
    "pid": integer(wrapper_pid),
    "wrapper_pid": integer(wrapper_pid),
    "optimizer_pid": integer(optimizer_pid),
    "outer_round": integer(outer),
    "candidate_index": integer(replay),
    "accepted_shrink_count": integer(accepted) or 0,
    "accepted_shrink_target": 10,
    "real_replay_hard_max": 5,
    "location": location,
    "error": error or None,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
directory = os.path.dirname(path)
fd, temporary = tempfile.mkstemp(prefix=".run_state.", dir=directory, text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)
        handle.write("\n")
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}
append_event() {
    "$PYTHON" - "$PROGRESS_LOG" "$@" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, event, outer, replay, accepted, error = sys.argv[1:]
payload = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "event": event,
    "outer_round": int(outer) if outer else None,
    "candidate_index": int(replay) if replay else 0,
    "accepted_shrink_count": int(accepted) if accepted else 0,
}
if error:
    payload["error"] = error
with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
PY
}

MODE="${1:---preflight}"
[[ "$MODE" == "--preflight" || "$MODE" == "--authorize-deploy" ]] \
    || fail "usage: $0 [--preflight | --authorize-deploy TOKEN]"
[[ -z "${CANARY_MAX_OUTER_ROUNDS:-}" ]] || fail "CANARY_MAX_OUTER_ROUNDS is set"
[[ -z "${BONECT_FORCE_CANDIDATE_REPLAY_TOP_K:-}" ]] \
    || fail "BONECT_FORCE_CANDIDATE_REPLAY_TOP_K is set"
[[ -x "$PYTHON" ]] || fail "lane Python 3.11 is unavailable"
[[ -f "$PROPOSED" && -f "$EXPECTED_SHA_FILE" ]] || fail "proposal or SHA file missing"

EXPECTED_SHA=$(awk 'NF {print $1; exit}' "$EXPECTED_SHA_FILE")
ACTUAL_SHA=$(sha256sum "$PROPOSED" | awk '{print $1}')
[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{64}$ ]] || fail "malformed expected SHA"
[[ "$ACTUAL_SHA" == "$EXPECTED_SHA" ]] || fail "proposed config SHA mismatch"

"$PYTHON" - "$PROPOSED" "$EXPECTED_RUNTIME_VERSION" <<'PY'
import json
import os
import sys

cfg = json.load(open(sys.argv[1], encoding="utf-8"))
campaign = cfg["_campaign"]
outer = cfg["formal_outer_protocol"]
bf = cfg["optimizer"]["bruteforce"]
runtime = cfg["_tpch_runtime_resource_contract"]
gate = cfg["baseline_resource_band_gate"]
ranges = gate["ranges_pct"]
assert campaign["attempt_tag"] == "attempt48_global_replay_cap5_20260726"
root = campaign["candidate_protocol"]["measurement_evidence_root"]
assert "tpch_on_ap_slot_node01" in root and "node04" not in root.lower()
assert cfg["optimizer"]["mode"] == "bruteforce"
assert cfg["_tpch_attempt45_tsgd_contract"]["accepted_shrink_target"] == 10
assert outer["accepted_shrink_target"] == 10
assert outer["formal_search_accepted_target"] == 10
assert outer["formal_search_initial_replay_top_k"] == 5
assert outer["formal_search_second_replay_top_k"] == 5
assert outer["formal_search_max_replay_top_k"] == 5
assert bf["candidate_replay_top_k"] == 5
assert cfg["_algorithm_c_ratio_resource_only"]["inner_score"]["qps"] == "excluded"
assert runtime["version"] == sys.argv[2]
assert 0 < runtime["cpu_quota_cores"] <= 32
assert runtime["memory_limit_bytes"] > 0
assert runtime["memory_swap_limit_bytes"] == runtime["memory_limit_bytes"]
assert runtime["workers"] > 0
assert runtime["io_read_cap_mib_s"] > 0
assert runtime["io_write_cap_mib_s"] > 0
assert runtime["io_total_limit_mib_s"] == (
    runtime["io_read_cap_mib_s"] + runtime["io_write_cap_mib_s"]
)
assert runtime["resource_change_authorized"] is False
assert gate["enabled"] is True and gate["hard_fail"] is True
assert gate["target_resource"] == "memory"
assert ranges["memory"]["min_pct"] == 85.0
assert ranges["memory"]["max_pct"] == 90.0
for resource in ("cpu", "io"):
    assert ranges[resource]["min_pct"] == 30.0
    assert ranges[resource]["max_pct"] == 50.0
assert not os.environ.get("CANARY_MAX_OUTER_ROUNDS")
assert not os.environ.get("BONECT_FORCE_CANDIDATE_REPLAY_TOP_K")
print(
    "STATIC_CONFIG_PASS "
    f"cpu={runtime['cpu_quota_cores']} "
    f"memory={runtime['memory_limit_bytes']} "
    f"read={runtime['io_read_cap_mib_s']} "
    f"write={runtime['io_write_cap_mib_s']} "
    f"workers={runtime['workers']} accepted=10 top5=5"
)
PY

read -r TRIAL_CPU TRIAL_MEMORY TRIAL_WORKERS TRIAL_IO_READ TRIAL_IO_WRITE TRIAL_IO_TOTAL < <(
    "$PYTHON" - "$PROPOSED" <<'PY'
import json
import sys

runtime = json.load(open(sys.argv[1], encoding="utf-8"))[
    "_tpch_runtime_resource_contract"
]
print(
    runtime["cpu_quota_cores"],
    runtime["memory_limit_bytes"],
    runtime["workers"],
    runtime["io_read_cap_mib_s"],
    runtime["io_write_cap_mib_s"],
    runtime["io_total_limit_mib_s"],
)
PY
)

[[ -f "$RUNNER" && -f "$R0_VALIDATOR" && -f "$OPTIMIZER" ]] \
    || fail "runner, R0 profile validator, or optimizer missing"
[[ "$(sha256sum "$RUNNER" | awk '{print $1}')" == "$EXPECTED_RUNNER_SHA" ]] \
    || fail "rolling-profile runner SHA mismatch"
[[ -f "$LIVE" ]] || fail "live config missing"
[[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" == true ]] \
    || fail "container is not running"
CPUSET=$(docker inspect -f '{{.HostConfig.CpusetCpus}}' "$CONTAINER")
"$PYTHON" - "$CPUSET" "$TRIAL_CPU" <<'PY'
import sys

count = 0
for chunk in sys.argv[1].split(","):
    bounds = chunk.split("-", 1)
    count += int(bounds[-1]) - int(bounds[0]) + 1
quota = float(sys.argv[2])
if count < quota:
    raise SystemExit(f"CPU quota {quota:g} exceeds cpuset size {count}")
print(f"CPUSET_CAPACITY_PASS cpuset={sys.argv[1]} count={count} quota={quota:g}")
PY

if [[ "$MODE" == "--preflight" ]]; then
    printf 'PREFLIGHT_ONLY_PASS no changes made sha256=%s\n' "$ACTUAL_SHA"
    exit 0
fi

[[ "${2:-}" == "$AUTH_TOKEN" ]] || fail "explicit deployment authorization token missing"
[[ "${TPCH_RESOURCE_CHANGE_AUTHORIZED:-}" == "$AUTH_TOKEN" ]] \
    || fail "matching TPCH_RESOURCE_CHANGE_AUTHORIZED environment authorization missing"
if pgrep -af '[p]arameter_optimizer.py' | grep -F "$A48" >/dev/null; then
    fail "attempt48 optimizer already running"
fi

mkdir -p "$LOG_DIR" "$RUN_DIR"
PYTHON_SHIM_DIR="$RUN_DIR/python311-shim"
mkdir -p "$PYTHON_SHIM_DIR"
ln -sfn "$PYTHON" "$PYTHON_SHIM_DIR/python3"
ln -sfn "$PYTHON" "$PYTHON_SHIM_DIR/python"
export PATH="$PYTHON_SHIM_DIR:$(dirname "$PYTHON"):$PATH"
export PYTHON="$PYTHON"
export PYTHON3="$PYTHON"
export PYTHONPATH="$A48/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages"
export PYTHONNOUSERSITE=1
unset PYTHONHOME
[[ "$(command -v python3)" == "$PYTHON_SHIM_DIR/python3" ]] \
    || fail "python3 shim is not first in PATH"
POINT="$A48/candidate_measurements/workers${TRIAL_WORKERS}_resource_correction_$(date -u +%Y%m%dT%H%M%SZ)_$$"
WRAPPER_LOG="$LOG_DIR/attempt48_resource_correction_${RUN_TAG}_r0_then_optimizer.log"
OPTIMIZER_LOG="$LOG_DIR/attempt48_optimizer.log"
TRANSACTION_STATE="$RUN_DIR/attempt48_resource_correction.transaction"

run_transaction() (
    set -euo pipefail
    exec 9>"$RUN_DIR/attempt48_resource_correction.lock"
    flock -n 9 || fail "another resource-correction transaction holds the lock"

    read -r OLD_RUNNING OLD_NANO_CPUS OLD_MEMORY OLD_MEMORY_SWAP < <(
        docker inspect -f \
            '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' \
            "$CONTAINER"
    )
    [[ "$OLD_RUNNING" == true ]] || fail "container stopped before transaction"
    OLD_CPU_CORES=$("$PYTHON" - "$OLD_NANO_CPUS" <<'PY'
import sys

nano = int(sys.argv[1])
if nano < 0:
    raise SystemExit("negative NanoCpus is invalid")
value = nano / 1_000_000_000
print(f"{value:.9f}".rstrip("0").rstrip("."))
PY
)
    [[ -n "$OLD_CPU_CORES" ]] || fail "could not convert old NanoCpus"

    ORIGINAL_LIVE_SHA=$(sha256sum "$LIVE" | awk '{print $1}')
    BACKUP="$A48/config/live.pre_resource_correction.$(date -u +%Y%m%dT%H%M%SZ).json"
    cp --preserve=mode,timestamps "$LIVE" "$BACKUP"
    [[ "$(sha256sum "$BACKUP" | awk '{print $1}')" == "$ORIGINAL_LIVE_SHA" ]] \
        || fail "live config backup verification failed"

    RESOURCE_MUTATED=0
    CONFIG_ACTIVATED=0
    COMMITTED=0
    ACTIVE_CHILD=""

    rollback() {
        local original_rc=$?
        local rollback_failed=0
        local restore_tmp
        local now_running now_nano now_memory now_memory_swap
        trap - EXIT INT TERM HUP
        log "ROLLBACK_START original_rc=$original_rc resource_mutated=$RESOURCE_MUTATED config_activated=$CONFIG_ACTIVATED"

        if [[ -n "$ACTIVE_CHILD" ]] && kill -0 "$ACTIVE_CHILD" 2>/dev/null; then
            kill "$ACTIVE_CHILD" 2>/dev/null || true
            wait "$ACTIVE_CHILD" 2>/dev/null || true
        fi

        if [[ "$CONFIG_ACTIVATED" == 1 ]]; then
            restore_tmp="$A48/config/.live.rollback.$$"
            if cp --preserve=mode "$BACKUP" "$restore_tmp" \
                && mv -f "$restore_tmp" "$LIVE" \
                && [[ "$(sha256sum "$LIVE" | awk '{print $1}')" == "$ORIGINAL_LIVE_SHA" ]]; then
                log "ROLLBACK_CONFIG_PASS sha256=$ORIGINAL_LIVE_SHA"
            else
                rm -f "$restore_tmp"
                log "ROLLBACK_CONFIG_FAILED backup=$BACKUP expected_sha=$ORIGINAL_LIVE_SHA"
                rollback_failed=1
            fi
        fi

        if [[ "$RESOURCE_MUTATED" == 1 ]]; then
            if docker update \
                --cpus "$OLD_CPU_CORES" \
                --memory "$OLD_MEMORY" \
                --memory-swap "$OLD_MEMORY_SWAP" \
                "$CONTAINER" >/dev/null; then
                read -r now_running now_nano now_memory now_memory_swap < <(
                    docker inspect -f \
                        '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' \
                        "$CONTAINER"
                )
                if [[ "$now_running" == true \
                    && "$now_nano" == "$OLD_NANO_CPUS" \
                    && "$now_memory" == "$OLD_MEMORY" \
                    && "$now_memory_swap" == "$OLD_MEMORY_SWAP" ]]; then
                    log "ROLLBACK_RESOURCE_PASS cpu_nano=$OLD_NANO_CPUS memory=$OLD_MEMORY memory_swap=$OLD_MEMORY_SWAP"
                else
                    log "ROLLBACK_RESOURCE_VERIFY_FAILED actual=$now_running,$now_nano,$now_memory,$now_memory_swap"
                    rollback_failed=1
                fi
            else
                log "ROLLBACK_RESOURCE_UPDATE_FAILED cpu=$OLD_CPU_CORES memory=$OLD_MEMORY memory_swap=$OLD_MEMORY_SWAP"
                rollback_failed=1
            fi
        fi

        if ! write_state rolled_back rollback "$$" "" "" "" "0" "$A48" "original_rc=$original_rc"; then
            log "ROLLBACK_STATE_WRITE_FAILED"
        fi
        if ! append_event rollback "" "0" "0" "original_rc=$original_rc"; then
            log "ROLLBACK_PROGRESS_WRITE_FAILED"
        fi

        if [[ "$rollback_failed" == 1 ]]; then
            log "ROLLBACK_FATAL manual_recovery_state=$TRANSACTION_STATE backup=$BACKUP"
            exit 90
        fi
        rm -f "$TRANSACTION_STATE"
        log "ROLLBACK_PASS original_rc=$original_rc"
        exit "$original_rc"
    }
    trap rollback EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 129' HUP

    printf '%s\n' \
        "status=prepared" \
        "old_nano_cpus=$OLD_NANO_CPUS" \
        "old_cpu_cores=$OLD_CPU_CORES" \
        "old_memory=$OLD_MEMORY" \
        "old_memory_swap=$OLD_MEMORY_SWAP" \
        "original_live_sha=$ORIGINAL_LIVE_SHA" \
        "backup=$BACKUP" >"$TRANSACTION_STATE"
    printf '0\n' >"$ACCEPTED_COUNTER"
    write_state prepared authorization "$BASHPID" "" "" "0" "0" "$A48" "" \
        || fail "could not write monitor run_state"
    append_event authorization_waiting "" "0" "0" "" \
        || fail "could not write monitor progress"

    # Docker exposes no cross-filesystem transaction. Mark mutation first so a
    # partially failed daemon update still triggers an idempotent restoration.
    RESOURCE_MUTATED=1
    docker update \
        --cpus "$TRIAL_CPU" \
        --memory "$TRIAL_MEMORY" \
        --memory-swap "$TRIAL_MEMORY" \
        "$CONTAINER" >/dev/null
    read -r RUNNING NANO_CPUS MEMORY MEMORY_SWAP < <(
        docker inspect -f \
            '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' \
            "$CONTAINER"
    )
    [[ "$RUNNING" == true ]] || fail "container stopped after resource update"
    EXPECTED_NANO_CPUS=$("$PYTHON" - "$TRIAL_CPU" <<'PY'
from decimal import Decimal
import sys

print(int(Decimal(sys.argv[1]) * Decimal(1_000_000_000)))
PY
)
    [[ "$NANO_CPUS" == "$EXPECTED_NANO_CPUS" ]] || fail "post-update CPU mismatch"
    [[ "$MEMORY" == "$TRIAL_MEMORY" && "$MEMORY_SWAP" == "$TRIAL_MEMORY" ]] \
        || fail "post-update memory mismatch"
    log "RESOURCE_TRIAL_PASS cpu=$TRIAL_CPU memory=$TRIAL_MEMORY memory_swap=$TRIAL_MEMORY read=$TRIAL_IO_READ write=$TRIAL_IO_WRITE workers=$TRIAL_WORKERS"
    write_state running resource_trial "$BASHPID" "" "" "0" "0" "$A48" "" \
        || fail "could not update monitor run_state"
    append_event resource_trial "" "0" "0" "" \
        || fail "could not update monitor progress"

    write_state running r0_running "$BASHPID" "" "" "0" "0" "$POINT" "" \
        || fail "could not update monitor run_state"
    append_event r0_started "" "0" "0" "" \
        || fail "could not update monitor progress"

    if ! "$PYTHON" -S -u "$RUNNER" \
        --port 63301 \
        --database template_fullgrid_tpch_pg5 \
        --container "$CONTAINER" \
        --sql-dir "$A48/code/queries" \
        --output-dir "$POINT" \
        --workers "$TRIAL_WORKERS" \
        --warmup-seconds 180 \
        --session-work-mem 1MB \
        --statement-timeout-ms 900000 \
        --minimum-seconds 180 \
        --cpu-limit-cores "$TRIAL_CPU" \
        --memory-limit-bytes "$TRIAL_MEMORY" \
        --io-total-limit-mib-s "$TRIAL_IO_TOTAL" \
        --io-read-cap-mib-s "$TRIAL_IO_READ" \
        --io-write-cap-mib-s "$TRIAL_IO_WRITE" \
        --r0-target-resource memory \
        --r0-target-min-pct 85 \
        --r0-target-max-pct 90 \
        --stability-target-resource memory; then
        log "R0_FAILED point=$POINT action=rollback"
        exit 80
    fi
    [[ -f "$POINT/summary.json" ]] || { log "R0_SUMMARY_MISSING point=$POINT action=rollback"; exit 81; }
    log "R0_COMMAND_PASS summary=$POINT/summary.json"
    append_event r0_command_pass "" "0" "0" "" \
        || fail "could not update monitor progress"
    if ! "$PYTHON" "$R0_VALIDATOR" "$POINT/summary.json"; then
        log "R0_PROFILE_GATE_FAILED summary=$POINT/summary.json action=rollback"
        exit 83
    fi
    log "R0_PROFILE_GATE_PASS summary=$POINT/summary.json"
    append_event r0_profile_gate_pass "" "0" "0" "" \
        || fail "could not update monitor progress"

    LIVE_TMP="$A48/config/.live.resource-correction.$$"
    cp --preserve=mode "$PROPOSED" "$LIVE_TMP"
    [[ "$(sha256sum "$LIVE_TMP" | awk '{print $1}')" == "$ACTUAL_SHA" ]] \
        || fail "staged live config SHA mismatch"
    # Set before rename so interruption immediately after mv still restores.
    CONFIG_ACTIVATED=1
    mv -f "$LIVE_TMP" "$LIVE"
    [[ "$(sha256sum "$LIVE" | awk '{print $1}')" == "$ACTUAL_SHA" ]] \
        || fail "activated live config SHA mismatch"
    log "CONFIG_ACTIVATED_ATOMIC sha256=$ACTUAL_SHA backup=$BACKUP"
    write_state running optimizer_starting "$BASHPID" "" "" "0" "0" "$LIVE" "" \
        || fail "could not update monitor run_state"
    append_event config_activated "" "0" "0" "" \
        || fail "could not update monitor progress"

    env -u CANARY_MAX_OUTER_ROUNDS -u BONECT_FORCE_CANDIDATE_REPLAY_TOP_K \
        OPTIMIZER_CONFIG="$LIVE" TPC_H_CONFIG="$LIVE" CONFIG_PATH="$LIVE" LIVE_CONFIG="$LIVE" \
        nohup "$PYTHON" -u "$OPTIMIZER" >>"$OPTIMIZER_LOG" 2>&1 &
    ACTIVE_CHILD=$!
    printf '%s\n' "$ACTIVE_CHILD" >"$RUN_DIR/attempt48_optimizer.pid"
    sleep 2
    kill -0 "$ACTIVE_CHILD" 2>/dev/null || { log "OPTIMIZER_EARLY_EXIT action=rollback"; exit 82; }
    write_state running optimizer_running "$BASHPID" "$ACTIVE_CHILD" "0" "0" "0" "$LIVE" "" \
        || fail "could not update monitor run_state"
    append_event optimizer_started "0" "0" "0" "" \
        || fail "could not update monitor progress"

    COMMITTED=1
    printf '%s\n' \
        "status=committed" \
        "optimizer_pid=$ACTIVE_CHILD" \
        "live_sha=$ACTUAL_SHA" \
        "backup=$BACKUP" >"$TRANSACTION_STATE"
    write_state committed optimizer_running "$BASHPID" "$ACTIVE_CHILD" "0" "0" "0" "$LIVE" "" \
        || fail "could not finalize monitor run_state"
    append_event transaction_commit "0" "0" "0" "" \
        || fail "could not finalize monitor progress"
    trap - EXIT INT TERM HUP
    log "TRANSACTION_COMMIT optimizer_pid=$ACTIVE_CHILD live_sha=$ACTUAL_SHA"
)

run_transaction >>"$WRAPPER_LOG" 2>&1 &
PID=$!
printf '%s\n' "$PID" >"$RUN_DIR/attempt48_resource_correction_${RUN_TAG}_wrapper.pid"
printf 'AUTHORIZED_WRAPPER_STARTED pid=%s log=%s point=%s backup=%s\n' \
    "$PID" "$WRAPPER_LOG" "$POINT" "recorded-in-transaction-state"
