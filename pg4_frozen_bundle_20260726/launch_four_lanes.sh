#!/usr/bin/env bash
set -uo pipefail

# Run this script on compute00. Every lane is started independently; one failure
# does not prevent the other lanes from starting.
BUNDLE_ROOT=${BUNDLE_ROOT:-/home/anonymous/program/resource_adaptive/pg4_frozen_bundle_20260726}
RUN_ROOT=${RUN_ROOT:-$BUNDLE_ROOT/run}
mkdir -p "$RUN_ROOT"

JOB_HOST=anonymous@192.0.2.4
TPCH_HOST=anonymous@192.0.2.3
JOB_ROOT=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02/history/job/outer/attempt_7_cap5_contract_20260726
TPCH_ROOT=/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726
TPCC_ROOT=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726
XP_ROOT=/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/hybench_xp_compute00_59630/experience_sgd/attempt39_fresh_from_accepted3_20260726

declare -A RESULT

record() {
    local lane=$1 status=$2 detail=$3
    RESULT["$lane"]="$status|$detail"
    printf '%s lane=%s status=%s detail=%s\n' "$(date --iso-8601=seconds)" "$lane" "$status" "$detail"
}

start_job() {
    local command
    command="set -euo pipefail; root='$JOB_ROOT'; launcher=\"\$root/launch_attempt7_cap5_canary.sh\"; log=\"\$root/frozen_bundle_launch.log\"; pidfile=\"\$root/frozen_bundle_launch.pid\"; family=\"\$root/candidate_families/job-resource-frontier-v1/manifest.json\"; consumed=\"\$root/run/job-resource-frontier-v1.consumed.sha256\"; if [[ -s \"\$pidfile\" ]] && kill -0 \"\$(cat \"\$pidfile\")\" 2>/dev/null; then echo ALREADY_RUNNING pid=\$(cat \"\$pidfile\"); exit 0; fi; latest=\$(ls -1t \"\$root\"/optimizer_history_*.jsonl 2>/dev/null | head -1 || true); if [[ -n \"\$latest\" ]] && grep -q '\"candidate_space_exhausted\": true' \"\$latest\"; then if [[ ! -s \"\$family\" ]]; then echo SKIPPED_UNCHANGED_EXHAUSTED_SPACE reason=missing_new_candidate_family; exit 0; fi; family_sha=\$(sha256sum \"\$family\" | awk '{print \$1}'); if [[ -s \"\$consumed\" ]] && [[ \"\$(cat \"\$consumed\")\" == \"\$family_sha\" ]]; then echo SKIPPED_UNCHANGED_EXHAUSTED_SPACE family_sha=\$family_sha; exit 0; fi; fi; test -x \"\$launcher\"; nohup setsid bash \"\$launcher\" >>\"\$log\" 2>&1 </dev/null & pid=\$!; echo \$pid >\"\$pidfile\"; sleep 2; if ! kill -0 \$pid 2>/dev/null; then wait \$pid 2>/dev/null || rc=\$?; rm -f \"\$pidfile\"; echo LAUNCHER_EXITED pid=\$pid rc=\${rc:-1} log=\$log >&2; exit \${rc:-1}; fi; echo STARTED pid=\$pid log=\$log"
    if output=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$JOB_HOST" "$command" 2>&1); then
        record JOB started "$output"
    else
        record JOB failed "$output"
    fi
}

start_tpch() {
    local command
    command="set -euo pipefail; root='$TPCH_ROOT'; launcher=\"\$root/code/attempt48_corrected_r0_only.sh\"; preflight=\"\$root/code/attempt48_corrected_r0_only.preflight.ok\"; log=\"\$root/logs/attempt48_corrected_r0_only.log\"; pidfile=\"\$root/run/attempt48_corrected_r0_only.pid\"; if [[ -s \"\$pidfile\" ]] && kill -0 \"\$(cat \"\$pidfile\")\" 2>/dev/null; then echo ALREADY_RUNNING pid=\$(cat \"\$pidfile\"); exit 0; fi; rm -f \"\$pidfile\"; if [[ ! -x \"\$launcher\" || ! -s \"\$preflight\" ]]; then echo BLOCKED_CORRECTED_R0_NOT_DEPLOYED; exit 0; fi; grep -qx 'cpu_cores=15.5' \"\$preflight\"; grep -qx 'memory_bytes=2243000000' \"\$preflight\"; grep -qx 'workers=36' \"\$preflight\"; grep -qx 'optimizer_start=false' \"\$preflight\"; mkdir -p \"\$root/logs\" \"\$root/run\"; nohup setsid bash \"\$launcher\" >>\"\$log\" 2>&1 </dev/null & pid=\$!; echo \$pid >\"\$pidfile\"; sleep 2; if ! kill -0 \$pid 2>/dev/null; then wait \$pid 2>/dev/null || rc=\$?; [[ \"\$(cat \"\$pidfile\" 2>/dev/null || true)\" != \"\$pid\" ]] || rm -f \"\$pidfile\"; echo LAUNCHER_EXITED pid=\$pid rc=\${rc:-1} log=\$log >&2; exit \${rc:-1}; fi; echo STARTED_CORRECTED_R0_ONLY pid=\$pid log=\$log"
    if output=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$TPCH_HOST" "$command" 2>&1); then
        record TPC-H started "$output"
    else
        record TPC-H failed "$output"
    fi
}

start_tpcc() {
    local unit=pg5-live-tpcc-a12-uncertainty.service
    local runner=/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/shell/run_tpcc_sensitive_mix.py
    local expected_sha=0de61169178fc39e936a34f4a8ac2440fd8c276e47eeca4f948538bba3105fbb
    local actual_sha active pid
    actual_sha=$(sha256sum "$runner" 2>/dev/null | awk '{print $1}')
    if [[ "$actual_sha" != "$expected_sha" ]]; then
        record TPC-C failed "runner_sha_mismatch expected=$expected_sha actual=${actual_sha:-missing}; run $BUNDLE_ROOT/fix_tpcc_and_restart.sh"
        return
    fi
    active=$(sudo systemctl show "$unit" -p ActiveState --value 2>/dev/null || true)
    pid=$(sudo systemctl show "$unit" -p MainPID --value 2>/dev/null || true)
    if [[ "$active" == active && "$pid" =~ ^[1-9][0-9]*$ ]]; then
        record TPC-C started "ALREADY_RUNNING pid=$pid"
        return
    fi
    sudo systemctl stop "$unit" 2>/dev/null || true
    sudo systemctl reset-failed "$unit" 2>/dev/null || true
    if output=$(sudo systemd-run --unit="$unit" --uid=anonymous --gid=anonymous \
        --working-directory="$TPCC_ROOT" --property=KillMode=control-group \
        --property=Restart=no "$TPCC_ROOT/run_with_joblib.sh" 2>&1); then
        sleep 2
        active=$(sudo systemctl show "$unit" -p ActiveState --value 2>/dev/null || true)
        pid=$(sudo systemctl show "$unit" -p MainPID --value 2>/dev/null || true)
        if [[ "$active" == active && "$pid" =~ ^[1-9][0-9]*$ ]]; then
            record TPC-C started "pid=$pid unit=$unit"
        else
            record TPC-C failed "unit_not_active state=$active pid=$pid output=$output"
        fi
    else
        record TPC-C failed "$output"
    fi
}

start_xp() {
    local launcher="$XP_ROOT/run_round29_novel_batch.sh"
    local scanner="$XP_ROOT/repair/xp_round29_novel_scan.py"
    local manifest="$XP_ROOT/config/xp_round29_required_artifacts.json"
    local log="$XP_ROOT/output/round29_novel_batch.log"
    local pidfile="$XP_ROOT/output/round29_novel_batch.pid"
    if [[ -s "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        record XP started "ALREADY_RUNNING pid=$(cat "$pidfile")"
        return
    fi
    if [[ ! -x "$launcher" || ! -s "$scanner" || ! -s "$manifest" ]]; then
        record XP blocked "BASIN_EXHAUSTED missing_sha_locked_round29_artifacts"
        return
    fi
    nohup setsid bash "$launcher" >>"$log" 2>&1 </dev/null &
    local pid=$!
    echo "$pid" >"$pidfile"
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        record XP started "pid=$pid log=$log"
    else
        record XP failed "launcher_exited log=$log"
    fi
}

case "${1:-all}" in
    all)
        # Keep all remote control traffic serial. Each lane is independently
        # fail-closed, so a blocked lane does not prevent later lanes.
        start_job
        start_tpch
        start_tpcc
        start_xp
        ;;
    job) start_job ;;
    tpch) start_tpch ;;
    tpcc) start_tpcc ;;
    xp) start_xp ;;
    *) echo "usage: $0 [all|job|tpch|tpcc|xp]" >&2; exit 64 ;;
esac

printf '\nRun the five-minute monitor with:\n  %s/monitor_four_lanes_5min.py\n' "$BUNDLE_ROOT"
