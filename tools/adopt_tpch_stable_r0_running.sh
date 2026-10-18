#!/usr/bin/env bash
set -euo pipefail

BASE=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
CODE="$BASE/code"
RUN="$BASE/run"
LIVE="$BASE/config/live.json"
OPTIMIZER="$CODE/parameter_optimizer.py"
HELPER="$CODE/stable_r0_baseline.py"
TEST="$CODE/test_stable_r0_baseline.py"
WORKLOAD="$CODE/preflight/run_tpch_weighted_point_attempt48_cpu32_cachefixed.sh"
LAUNCHER="$CODE/launch_tpch_formal_after_workload_binding_fix.sh"
STATE="$RUN/attempt48_formal10_start.state.json"
AUDIT="$RUN/attempt48_stable_r0_running_adoption.audit.json"

NEW_LIVE="$BASE/config/live.v10-unified-stable-r0.staged.json"
NEW_OPTIMIZER="$OPTIMIZER.stable_r0_staged"
NEW_HELPER="$HELPER.staged"
NEW_TEST="$TEST.staged"
NEW_WORKLOAD="$WORKLOAD.stable_r0_staged"
NEW_LAUNCHER="$LAUNCHER.stable_r0_staged"

OLD_LIVE_SHA=2155152de797fb8fdf9c7c62ba53513731dfc70e72787e119ba1e82c380e323a
OLD_OPTIMIZER_SHA=f9eaedbc9db6215b3cbb6e4e9f9b1f164b357f0c5c97f42a9cc4f3051bdad777
OLD_WORKLOAD_SHA=fe847f67f11d37b931462feeef9dc07508f616a2f8ece76c4d6e5deb3d214c61
OLD_LAUNCHER_SHA=b8578073b5cab39a6455b5b31cec5b2919d4c7a091dcbf5c97983e6700e68b8d

NEW_LIVE_SHA=23da90cdb9123c76a0bbf2a39a8899b43de955ed4cdf63d7b4abb098e060b380
NEW_OPTIMIZER_SHA=030d5283be85bdb599e0cf76a1fbe274fc2a1186f17532f48119d846a9c75057
NEW_HELPER_SHA=719d8dedb2ea31bff4ed8e38a2d8b0bba4ff26a05ddbef1db5c7d16ef10374da
NEW_TEST_SHA=9b9853b443d8e1a47200eae903619fbeff28f1614d2e0fd0b2a3de84fe0c3dc9
NEW_WORKLOAD_SHA=6ff25f942687cfd19a93a3c7b52d701351910d18a9de6aa8dc18900a6adb0a51
NEW_LAUNCHER_SHA=ad863468fcf12edf4a74d25084dc99f2b895176fe3f7c072ae61efb807648285

sha() { sha256sum "$1" | awk '{print $1}'; }
fail() { printf 'ERROR %s\n' "$*" >&2; exit 1; }

mapfile -t OPTIMIZER_PIDS < <(
    pgrep -af '[p]arameter_optimizer.py' | grep -F "$BASE" | awk '{print $1}'
)
mapfile -t WORKLOAD_PIDS < <(
    pgrep -af '[r]un_pg_weighted_sql_baseline_profile_rolling_v2.py' \
        | grep -F "$BASE" | awk '{print $1}'
)
[[ "${#OPTIMIZER_PIDS[@]}" == 1 && "${#WORKLOAD_PIDS[@]}" == 1 ]] \
    || fail "expected exactly one optimizer and one rolling workload"
OPTIMIZER_PID=${OPTIMIZER_PIDS[0]}
WORKLOAD_PID=${WORKLOAD_PIDS[0]}
[[ "$(ps -o ppid= -p "$WORKLOAD_PID" | tr -d ' ')" == "$OPTIMIZER_PID" ]] \
    || fail "rolling workload is not a direct optimizer child"
OPTIMIZER_CMD=$(tr '\0' ' ' <"/proc/$OPTIMIZER_PID/cmdline")
WORKLOAD_CMD=$(tr '\0' ' ' <"/proc/$WORKLOAD_PID/cmdline")
[[ "$OPTIMIZER_CMD" == *"$BASE/code/parameter_optimizer.py"* ]] \
    || fail "optimizer command mismatch"
[[ "$WORKLOAD_CMD" == *"--warmup-seconds 180"* \
    && "$WORKLOAD_CMD" == *"--minimum-seconds 180"* \
    && "$WORKLOAD_CMD" == *"--maximum-seconds 600"* \
    && "$WORKLOAD_CMD" == *"--cpu-limit-cores 22"* \
    && "$WORKLOAD_CMD" == *"--memory-limit-bytes 1730000000"* ]] \
    || fail "rolling workload contract mismatch"

[[ "$(sha "$LIVE")" == "$OLD_LIVE_SHA" ]] || fail "rolled-back live SHA mismatch"
[[ "$(sha "$OPTIMIZER")" == "$OLD_OPTIMIZER_SHA" ]] || fail "rolled-back optimizer SHA mismatch"
[[ "$(sha "$WORKLOAD")" == "$OLD_WORKLOAD_SHA" ]] || fail "rolled-back workload SHA mismatch"
[[ "$(sha "$LAUNCHER")" == "$OLD_LAUNCHER_SHA" ]] || fail "rolled-back launcher SHA mismatch"
for spec in \
    "$NEW_LIVE:$NEW_LIVE_SHA" \
    "$NEW_OPTIMIZER:$NEW_OPTIMIZER_SHA" \
    "$NEW_HELPER:$NEW_HELPER_SHA" \
    "$NEW_TEST:$NEW_TEST_SHA" \
    "$NEW_WORKLOAD:$NEW_WORKLOAD_SHA" \
    "$NEW_LAUNCHER:$NEW_LAUNCHER_SHA"; do
    file=${spec%%:*}
    expected=${spec##*:}
    [[ -f "$file" && "$(sha "$file")" == "$expected" ]] \
        || fail "staged SHA mismatch: $file"
done

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
LIVE_BACKUP="$LIVE.pre_running_adoption.$STAMP"
OPTIMIZER_BACKUP="$OPTIMIZER.pre_running_adoption.$STAMP"
WORKLOAD_BACKUP="$WORKLOAD.pre_running_adoption.$STAMP"
LAUNCHER_BACKUP="$LAUNCHER.pre_running_adoption.$STAMP"
cp --preserve=mode,timestamps "$LIVE" "$LIVE_BACKUP"
cp --preserve=mode,timestamps "$OPTIMIZER" "$OPTIMIZER_BACKUP"
cp --preserve=mode,timestamps "$WORKLOAD" "$WORKLOAD_BACKUP"
cp --preserve=mode,timestamps "$LAUNCHER" "$LAUNCHER_BACKUP"

INSTALLED=0
COMMITTED=0
rollback() {
    local rc=$?
    trap - EXIT INT TERM HUP
    if [[ "$COMMITTED" == 1 || "$INSTALLED" == 0 ]]; then
        exit "$rc"
    fi
    kill "$OPTIMIZER_PID" 2>/dev/null || true
    kill "$WORKLOAD_PID" 2>/dev/null || true
    wait "$OPTIMIZER_PID" 2>/dev/null || true
    wait "$WORKLOAD_PID" 2>/dev/null || true
    cp --preserve=mode "$LIVE_BACKUP" "$LIVE"
    cp --preserve=mode "$OPTIMIZER_BACKUP" "$OPTIMIZER"
    cp --preserve=mode "$WORKLOAD_BACKUP" "$WORKLOAD"
    cp --preserve=mode "$LAUNCHER_BACKUP" "$LAUNCHER"
    exit "$rc"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

INSTALLED=1
install -m 0644 "$NEW_HELPER" "$HELPER"
install -m 0644 "$NEW_TEST" "$TEST"
install -m 0644 "$NEW_OPTIMIZER" "$OPTIMIZER"
install -m 0755 "$NEW_WORKLOAD" "$WORKLOAD"
install -m 0755 "$NEW_LAUNCHER" "$LAUNCHER"
LIVE_TEMP="$BASE/config/.live.running-adoption.$$"
cp --preserve=mode "$NEW_LIVE" "$LIVE_TEMP"
mv -f "$LIVE_TEMP" "$LIVE"

[[ "$(sha "$LIVE")" == "$NEW_LIVE_SHA" ]] || fail "adopted live SHA mismatch"
[[ "$(sha "$OPTIMIZER")" == "$NEW_OPTIMIZER_SHA" ]] || fail "adopted optimizer SHA mismatch"
[[ "$(sha "$HELPER")" == "$NEW_HELPER_SHA" ]] || fail "adopted helper SHA mismatch"
[[ "$(sha "$WORKLOAD")" == "$NEW_WORKLOAD_SHA" ]] || fail "adopted workload SHA mismatch"
[[ "$(sha "$LAUNCHER")" == "$NEW_LAUNCHER_SHA" ]] || fail "adopted launcher SHA mismatch"
kill -0 "$OPTIMIZER_PID" 2>/dev/null || fail "optimizer exited during adoption"
kill -0 "$WORKLOAD_PID" 2>/dev/null || fail "workload exited during adoption"

"$PYTHON" - "$STATE" "$AUDIT" "$OPTIMIZER_PID" "$WORKLOAD_PID" "$STAMP" <<'PY'
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

state_path, audit_path, optimizer_pid, workload_pid, stamp = sys.argv[1:]
now = datetime.now(timezone.utc).isoformat()

def atomic(path, payload):
    fd, temporary = tempfile.mkstemp(
        prefix=".tpch-running-adoption.", dir=os.path.dirname(path), text=True
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
    "optimizer_pid": int(optimizer_pid),
    "workload_pid": int(workload_pid),
    "formal_accepted_count": 0,
    "accepted_shrink_target": 10,
    "updated_at": now,
})
atomic(audit_path, {
    "schema_version": "tpch_stable_r0_running_adoption_v1",
    "status": "committed",
    "optimizer_pid": int(optimizer_pid),
    "workload_pid": int(workload_pid),
    "new_workload_invocations": 0,
    "adopted_existing_invocation": True,
    "live_sha256": "23da90cdb9123c76a0bbf2a39a8899b43de955ed4cdf63d7b4abb098e060b380",
    "optimizer_sha256": "030d5283be85bdb599e0cf76a1fbe274fc2a1186f17532f48119d846a9c75057",
    "helper_sha256": "719d8dedb2ea31bff4ed8e38a2d8b0bba4ff26a05ddbef1db5c7d16ef10374da",
    "workload_sha256": "6ff25f942687cfd19a93a3c7b52d701351910d18a9de6aa8dc18900a6adb0a51",
    "backup_stamp": stamp,
    "committed_at": now,
})
PY

COMMITTED=1
trap - EXIT INT TERM HUP
printf 'RUNNING_STABLE_R0_ADOPTED optimizer_pid=%s workload_pid=%s\n' \
    "$OPTIMIZER_PID" "$WORKLOAD_PID"
