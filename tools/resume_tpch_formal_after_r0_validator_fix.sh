#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
CONTAINER=pg5_tpch_ap_slot_node01_20260726
POINT="$BASE/candidate_measurements/workers36_resource_correction_20260729T033954Z_91874"
SUMMARY="$POINT/summary.json"
DECISIONS="$POINT/stability_decisions.jsonl"
PROPOSED="$BASE/config/live.attempt48.resource-correction-22c1730m16r20w36w-node01.proposed.json"
LIVE="$BASE/config/live.json"
VALIDATOR="$BASE/code/validate_tpch_r0_profile.py"
OPTIMIZER="$BASE/code/parameter_optimizer.py"
AUTH_SOURCE="$BASE/gradient_debug_round17_bound_generic_custom_v1_20260729/run_controller.sh"
RUN_DIR="$BASE/run"
LOG_DIR="$BASE/logs"
STATE="$RUN_DIR/attempt48_r0_validator_zero_call_resume.state.json"
AUDIT="$RUN_DIR/attempt48_r0_validator_zero_call_resume.audit.json"
TRANSACTION="$RUN_DIR/attempt48_r0_validator_zero_call_resume.transaction"

EXPECTED_SUMMARY_SHA=ef2c5e9e419f6f17e7c9de96a0526da0438f90f86afec08ed7a6aa8fecc94860
EXPECTED_DECISIONS_SHA=9efd03bd403d6a986e563ff892f05da0abaf7a81ed3c3d38ac987b57869d36d6
EXPECTED_PROPOSED_SHA=2155152de797fb8fdf9c7c62ba53513731dfc70e72787e119ba1e82c380e323a
EXPECTED_LIVE_SHA=54a873caf467361572cd7b8e132294a2650fabeaaa554fb45fb3cc9b30c33ac6
EXPECTED_VALIDATOR_SHA=c24711ebaaa58ec8a41d036771e9287106883c786c67f283780eb49716532538
EXPECTED_AUTH_SOURCE_SHA=3216c95823218395b50ba23e8d7c82c83f0e83db3027b4b683c67ce2c281bc2d

fail() { printf 'ERROR %s\n' "$*" >&2; exit 1; }
sha() { sha256sum "$1" | awk '{print $1}'; }
atomic_state() {
    local status=$1
    local stage=$2
    local optimizer_pid=${3:-}
    local error=${4:-}
    "$PYTHON" - "$STATE" "$status" "$stage" "$optimizer_pid" "$error" <<'PY'
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

path, status, stage, optimizer_pid, error = sys.argv[1:]
payload = {
    "schema_version": "tpch_r0_validator_zero_call_resume_v1",
    "status": status,
    "stage": stage,
    "optimizer_pid": int(optimizer_pid) if optimizer_pid else None,
    "accepted_shrink_count": 0,
    "accepted_shrink_target": 10,
    "new_real_workload_invocations": 0,
    "evidence_summary_sha256": "ef2c5e9e419f6f17e7c9de96a0526da0438f90f86afec08ed7a6aa8fecc94860",
    "error": error or None,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
fd, temporary = tempfile.mkstemp(
    prefix=".tpch-r0-resume.", dir=os.path.dirname(path), text=True
)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

mkdir -p "$RUN_DIR" "$LOG_DIR"
exec 9>"$RUN_DIR/attempt48_resource_correction.lock"
flock -n 9 || fail "resource-correction transaction lock is held"

[[ -x "$PYTHON" && -f "$VALIDATOR" && -f "$OPTIMIZER" ]] \
    || fail "required Python, validator, or optimizer is missing"
[[ "$(sha "$AUTH_SOURCE")" == "$EXPECTED_AUTH_SOURCE_SHA" ]] \
    || fail "credential source SHA mismatch"
[[ "$(sha "$SUMMARY")" == "$EXPECTED_SUMMARY_SHA" ]] || fail "summary SHA mismatch"
[[ "$(sha "$DECISIONS")" == "$EXPECTED_DECISIONS_SHA" ]] || fail "decision SHA mismatch"
[[ "$(sha "$PROPOSED")" == "$EXPECTED_PROPOSED_SHA" ]] || fail "proposal SHA mismatch"
[[ "$(sha "$LIVE")" == "$EXPECTED_LIVE_SHA" ]] || fail "live config SHA mismatch"
[[ "$(sha "$VALIDATOR")" == "$EXPECTED_VALIDATOR_SHA" ]] || fail "validator SHA mismatch"
[[ "$(docker inspect -f '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' "$CONTAINER")" \
    == "true 46000000000 1750000000 1750000000" ]] || fail "rollback resource state mismatch"
if pgrep -af '[p]arameter_optimizer.py' | grep -F "$BASE" >/dev/null; then
    fail "attempt48 optimizer already running"
fi

"$PYTHON" "$VALIDATOR" "$SUMMARY" \
    | grep -Fx 'R0_PROFILE_GATE_PASS cpu=47.391537 memory=89.250529 io=40.622662' >/dev/null \
    || fail "fixed validator did not accept exact frozen evidence"

BACKUP="$BASE/config/live.pre_r0_validator_resume.$(date -u +%Y%m%dT%H%M%SZ).json"
cp --preserve=mode,timestamps "$LIVE" "$BACKUP"
[[ "$(sha "$BACKUP")" == "$EXPECTED_LIVE_SHA" ]] || fail "live backup SHA mismatch"

RESOURCE_MUTATED=0
CONFIG_ACTIVATED=0
COMMITTED=0
CHILD=
rollback() {
    local rc=$?
    trap - EXIT INT TERM HUP
    if [[ "$COMMITTED" == 1 ]]; then
        exit "$rc"
    fi
    if [[ -n "$CHILD" ]] && kill -0 "$CHILD" 2>/dev/null; then
        kill "$CHILD" 2>/dev/null || true
        wait "$CHILD" 2>/dev/null || true
    fi
    if [[ "$CONFIG_ACTIVATED" == 1 ]]; then
        local restore="$BASE/config/.live.r0-validator-rollback.$$"
        cp --preserve=mode "$BACKUP" "$restore"
        mv -f "$restore" "$LIVE"
        [[ "$(sha "$LIVE")" == "$EXPECTED_LIVE_SHA" ]] || exit 91
    fi
    if [[ "$RESOURCE_MUTATED" == 1 ]]; then
        docker update --cpus 46 --memory 1750000000 --memory-swap 1750000000 \
            "$CONTAINER" >/dev/null
        [[ "$(docker inspect -f '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' "$CONTAINER")" \
            == "true 46000000000 1750000000 1750000000" ]] || exit 92
    fi
    atomic_state rolled_back rollback "" "original_rc=$rc"
    exit "$rc"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

atomic_state running resource_activation
printf '%s\n' "prepared summary_sha=$EXPECTED_SUMMARY_SHA backup=$BACKUP" >"$TRANSACTION"
RESOURCE_MUTATED=1
docker update --cpus 22 --memory 1730000000 --memory-swap 1730000000 \
    "$CONTAINER" >/dev/null
[[ "$(docker inspect -f '{{.State.Running}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' "$CONTAINER")" \
    == "true 22000000000 1730000000 1730000000" ]] || fail "trial resource activation mismatch"

STAGED="$BASE/config/.live.r0-validator-resume.$$"
cp --preserve=mode "$PROPOSED" "$STAGED"
[[ "$(sha "$STAGED")" == "$EXPECTED_PROPOSED_SHA" ]] || fail "staged config SHA mismatch"
CONFIG_ACTIVATED=1
mv -f "$STAGED" "$LIVE"
[[ "$(sha "$LIVE")" == "$EXPECTED_PROPOSED_SHA" ]] || fail "activated config SHA mismatch"

SHIM="$RUN_DIR/python311-shim"
mkdir -p "$SHIM"
ln -sfn "$PYTHON" "$SHIM/python3"
ln -sfn "$PYTHON" "$SHIM/python"
export PATH="$SHIM:$(dirname "$PYTHON"):$PATH"
export PYTHON="$PYTHON"
export PYTHON3="$PYTHON"
export PYTHONPATH="$BASE/code:/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages"
export PYTHONNOUSERSITE=1
unset PYTHONHOME
PGPASSWORD_VALUE=$(sed -n 's/^export PGPASSWORD=//p' "$AUTH_SOURCE" | head -1)
[[ -n "$PGPASSWORD_VALUE" ]] || fail "trusted credential source has no PGPASSWORD"
case "$PGPASSWORD_VALUE" in
    *[!A-Za-z0-9_.-]*) fail "trusted PGPASSWORD contains unexpected characters" ;;
esac
export PGPASSWORD="$PGPASSWORD_VALUE"

atomic_state running optimizer_starting
env -u LD_LIBRARY_PATH -u CANARY_MAX_OUTER_ROUNDS -u BONECT_FORCE_CANDIDATE_REPLAY_TOP_K \
    OPTIMIZER_CONFIG="$LIVE" TPC_H_CONFIG="$LIVE" CONFIG_PATH="$LIVE" LIVE_CONFIG="$LIVE" \
    nohup "$PYTHON" -u "$OPTIMIZER" >>"$LOG_DIR/attempt48_optimizer.log" 2>&1 &
CHILD=$!
printf '%s\n' "$CHILD" >"$RUN_DIR/attempt48_optimizer.pid"
sleep 3
kill -0 "$CHILD" 2>/dev/null || fail "optimizer exited during startup"

"$PYTHON" - "$AUDIT" "$BACKUP" "$CHILD" <<'PY'
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

path, backup, pid = sys.argv[1:]
payload = {
    "schema_version": "tpch_r0_validator_zero_call_resume_audit_v1",
    "status": "committed",
    "new_real_workload_invocations": 0,
    "reclassified_valid_r0_measurements": 1,
    "physical_call_remains_consumed": True,
    "formal_accepted_added": 0,
    "optimizer_pid": int(pid),
    "summary_sha256": "ef2c5e9e419f6f17e7c9de96a0526da0438f90f86afec08ed7a6aa8fecc94860",
    "decisions_sha256": "9efd03bd403d6a986e563ff892f05da0abaf7a81ed3c3d38ac987b57869d36d6",
    "activated_config_sha256": "2155152de797fb8fdf9c7c62ba53513731dfc70e72787e119ba1e82c380e323a",
    "validator_sha256": "c24711ebaaa58ec8a41d036771e9287106883c786c67f283780eb49716532538",
    "backup": backup,
    "r0_utilization_pct": {
        "cpu": 47.39153672870637,
        "memory": 89.25052859601799,
        "io": 40.62266224410108,
    },
    "committed_at": datetime.now(timezone.utc).isoformat(),
}
fd, temporary = tempfile.mkstemp(
    prefix=".tpch-r0-resume-audit.", dir=os.path.dirname(path), text=True
)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY

COMMITTED=1
printf '%s\n' \
    "committed optimizer_pid=$CHILD live_sha=$EXPECTED_PROPOSED_SHA backup=$BACKUP" \
    >"$TRANSACTION"
atomic_state committed optimizer_running "$CHILD"
trap - EXIT INT TERM HUP
printf 'ZERO_CALL_RESUME_COMMIT optimizer_pid=%s live_sha=%s\n' \
    "$CHILD" "$EXPECTED_PROPOSED_SHA"
