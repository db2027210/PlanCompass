#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CONFIG=${CONFIG:-$ROOT_DIR/config/binary_plan_tuning_hybenchxp_vm_recall_5_10_20260716.json}
EXPERIMENT_STRATEGY=${EXPERIMENT_STRATEGY:-NESTED}
SCORE_B_TOPK=${SCORE_B_TOPK:-20}
EXPERIMENT_STRATEGY=${EXPERIMENT_STRATEGY^^}
case "$EXPERIMENT_STRATEGY" in
    A)
        STRATEGY_POLICY=small_loop_top1_score_v1
        STRATEGY_TOPK=1
        ;;
    B)
        STRATEGY_POLICY=real_score_b_successful_topk_v2
        STRATEGY_TOPK=$SCORE_B_TOPK
        if [[ ! "$STRATEGY_TOPK" =~ ^[0-9]+$ ]] || (( STRATEGY_TOPK <= 1 )); then
            echo "SCORE_B_TOPK must be an integer greater than one" >&2
            exit 2
        fi
        ;;
    C)
        STRATEGY_POLICY=real_score_c_no_qps_topk_v1
        STRATEGY_TOPK=$SCORE_B_TOPK
        if [[ ! "$STRATEGY_TOPK" =~ ^[0-9]+$ ]] || (( STRATEGY_TOPK <= 1 )); then
            echo "SCORE_B_TOPK must be an integer greater than one" >&2
            exit 2
        fi
        ;;
    NESTED)
        STRATEGY_POLICY=nested_inner_resource_outer_semantic_v1
        STRATEGY_TOPK=$SCORE_B_TOPK
        if [[ ! "$STRATEGY_TOPK" =~ ^[0-9]+$ ]] || (( STRATEGY_TOPK <= 1 )); then
            echo "SCORE_B_TOPK must be an integer greater than one" >&2
            exit 2
        fi
        ;;
    *)
        echo "EXPERIMENT_STRATEGY must be A, B, C, or NESTED" >&2
        exit 2
        ;;
esac
RUN_ID=${RUN_ID:-hyxp_four_vm_${EXPERIMENT_STRATEGY,,}_$(date +%Y%m%d_%H%M%S)}
RUN_ROOT=${RUN_ROOT:-/disk2/resource_adaptive/binary_plan_tuning_hybenchxp_vm/runs/$RUN_ID}
SESSION=${SESSION:-hyxp-four-vm-recall-${RUN_ID: -15}}
PREFLIGHT_DISK_SAMPLE_SEC=${PREFLIGHT_DISK_SAMPLE_SEC:-5}
PREFLIGHT_MAX_READ_MIB_S=${PREFLIGHT_MAX_READ_MIB_S:-5}
PREFLIGHT_MAX_WRITE_MIB_S=${PREFLIGHT_MAX_WRITE_MIB_S:-5}
PREFLIGHT_MAX_UTIL_PCT=${PREFLIGHT_MAX_UTIL_PCT:-10}
HOST_GUARD_MAX_DOCKER_MIB_S=${HOST_GUARD_MAX_DOCKER_MIB_S:-1}
HOST_GUARD_MAX_QEMU_MIB_S=${HOST_GUARD_MAX_QEMU_MIB_S:-1}
HOST_GUARD_INTERVAL_SEC=${HOST_GUARD_INTERVAL_SEC:-5}
ALLOWED_QEMU_GUESTS=${ALLOWED_QEMU_GUESTS:-bonect-replay-v87-1 bonect-replay-v87-2 bonect-replay-v87-3 bonect-replay-v87-4}
TARGETS=${TARGETS:-cpu memory io none}
FINAL_STOP_REASONS=${FINAL_STOP_REASONS:-epsilon_converged}
EXCLUSIVE_LOCK_PATH=${EXCLUSIVE_LOCK_PATH:-/disk2/resource_adaptive/binary_plan_tuning_hybenchxp_vm/\
FOUR_VM_EXCLUSIVE_DISK1.lock}

read -r -a TARGET_LIST <<<"$TARGETS"
read -r -a FINAL_STOP_REASON_LIST <<<"$FINAL_STOP_REASONS"
read -r -a ALLOWED_QEMU_GUEST_LIST <<<"$ALLOWED_QEMU_GUESTS"
if [[ ${#TARGET_LIST[@]} -eq 0 ]]; then
    echo "TARGETS must contain at least one target" >&2
    exit 2
fi

declare -A HOSTS=(
    [cpu]=192.0.2.11
    [memory]=192.0.2.12
    [io]=192.0.2.13
    [none]=192.0.2.14
)

mkdir -p "$RUN_ROOT"
if [[ -n "$(find "$RUN_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "run root is not empty: $RUN_ROOT" >&2
    exit 1
fi
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux session already exists: $SESSION" >&2
    exit 1
fi

if pgrep -af 'binary_plan_resource_tuner.py.*hyxp_io_mild_fullsearch_disk1x2' \
    >/dev/null; then
    echo "conflicting Docker IO search is still running" >&2
    exit 1
fi
# A running but idle PostgreSQL container is harmless and may intentionally be
# preserved for another resumable experiment.  Active interference is rejected
# by the process check above, the aggregate /disk1 preflight below, and the
# fail-closed per-container runtime guard.  Do not require unrelated containers
# to be stopped merely because their data directory is mounted from /disk1.

for target in "${TARGET_LIST[@]}"; do
    if [[ -z "${HOSTS[$target]+x}" ]]; then
        echo "unsupported target in TARGETS: $target" >&2
        exit 2
    fi
    vm_host=${HOSTS[$target]}
    if ! PGPASSWORD=CHANGE_ME psql \
        -h "$vm_host" -p 25432 -U postgres -d postgres -X -At -v ON_ERROR_STOP=1 \
        -c 'select 1' >/dev/null; then
        echo "VM PostgreSQL preflight failed: target=$target host=$vm_host" >&2
        exit 1
    fi
    client_count=$(PGPASSWORD=CHANGE_ME psql \
        -h "$vm_host" -p 25432 -U postgres -d postgres -X -At -v ON_ERROR_STOP=1 \
        -c "select count(*) from pg_stat_activity where pid<>pg_backend_pid() and backend_type='client backend';")
    if [[ "$client_count" != "0" ]]; then
        echo "VM has residual client backends: target=$target host=$vm_host count=$client_count" >&2
        exit 1
    fi
done

disk_source=$(findmnt -n -o SOURCE -T /disk1)
disk_device=${disk_source##*/}
read -r read_sectors_1 write_sectors_1 io_ms_1 < <(
    awk -v device="$disk_device" '$3 == device {print $6, $10, $13}' /proc/diskstats
)
sleep "$PREFLIGHT_DISK_SAMPLE_SEC"
read -r read_sectors_2 write_sectors_2 io_ms_2 < <(
    awk -v device="$disk_device" '$3 == device {print $6, $10, $13}' /proc/diskstats
)
read_mib_s=$(awk -v before="$read_sectors_1" -v after="$read_sectors_2" \
    -v seconds="$PREFLIGHT_DISK_SAMPLE_SEC" 'BEGIN {printf "%.6f", (after-before)*512/1048576/seconds}')
write_mib_s=$(awk -v before="$write_sectors_1" -v after="$write_sectors_2" \
    -v seconds="$PREFLIGHT_DISK_SAMPLE_SEC" 'BEGIN {printf "%.6f", (after-before)*512/1048576/seconds}')
util_pct=$(awk -v before="$io_ms_1" -v after="$io_ms_2" \
    -v seconds="$PREFLIGHT_DISK_SAMPLE_SEC" 'BEGIN {printf "%.6f", (after-before)/(seconds*1000)*100}')
if ! awk -v read="$read_mib_s" -v write="$write_mib_s" -v util="$util_pct" \
    -v max_read="$PREFLIGHT_MAX_READ_MIB_S" -v max_write="$PREFLIGHT_MAX_WRITE_MIB_S" \
    -v max_util="$PREFLIGHT_MAX_UTIL_PCT" \
    'BEGIN {exit !(read <= max_read && write <= max_write && util <= max_util)}'; then
    echo "shared VM disk is busy: device=$disk_device read=${read_mib_s}MiB/s " \
         "write=${write_mib_s}MiB/s util=${util_pct}%" >&2
    exit 1
fi

if [[ -s "$EXCLUSIVE_LOCK_PATH" ]]; then
    existing_session=$(awk -F= '$1 == "session" {print $2}' "$EXCLUSIVE_LOCK_PATH")
    if [[ -n "$existing_session" ]] && tmux has-session -t "$existing_session" 2>/dev/null; then
        echo "another four-VM run owns the disk lock: session=$existing_session" >&2
        exit 1
    fi
fi
mkdir -p "$(dirname "$EXCLUSIVE_LOCK_PATH")"
printf 'mode=four_vm_recall\nrun_root=%s\nsession=%s\ncreated_at=%s\n' \
    "$RUN_ROOT" "$SESSION" "$(date --iso-8601=seconds)" >"$EXCLUSIVE_LOCK_PATH"

launch_complete=false
cleanup_failed_launch() {
    if [[ "$launch_complete" == "true" ]]; then
        return
    fi
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    if grep -Fqx "run_root=$RUN_ROOT" "$EXCLUSIVE_LOCK_PATH" 2>/dev/null; then
        rm -f "$EXCLUSIVE_LOCK_PATH"
    fi
}
trap cleanup_failed_launch EXIT

SNAPSHOT_DIR="$RUN_ROOT/launch_snapshot"
mkdir -p "$SNAPSHOT_DIR"
FROZEN_SUPPORT_DIR="$SNAPSHOT_DIR/shell"
mkdir -p "$FROZEN_SUPPORT_DIR"
FROZEN_CONFIG="$SNAPSHOT_DIR/optimizer_config.json"
FROZEN_TUNER="$SNAPSHOT_DIR/binary_plan_resource_tuner.py"
FROZEN_PLAN_ESTIMATOR="$SNAPSHOT_DIR/plan_resource_estimator.py"
FROZEN_FEATURE_PREDICTOR="$SNAPSHOT_DIR/resource_feature_predictor.py"
FROZEN_PLAN_EXTRACTOR="$SNAPSHOT_DIR/extract_plan_trees.py"
FROZEN_INTERVAL_LINEAGE="$SNAPSHOT_DIR/bonect_interval_lineage.py"
FROZEN_RESOURCE_MONITOR="$SNAPSHOT_DIR/system_resource_monitor.py"
FROZEN_RESOURCE_UTILITY="$SNAPSHOT_DIR/resource_utility.py"
FROZEN_GUARD="$SNAPSHOT_DIR/watch_vm_host_disk_conflicts.py"
FROZEN_MONITOR="$SNAPSHOT_DIR/watch_vm_binary_search_best_so_far.py"
FROZEN_COMPLETION_WATCHER="$SNAPSHOT_DIR/watch_hybenchxp_four_vm_recall_completion.py"
FROZEN_AUDIT="$SNAPSHOT_DIR/audit_hybenchxp_four_vm_recall_completion.py"
FROZEN_SUPERVISOR="$FROZEN_SUPPORT_DIR/supervise_hybenchxp_vm_binary_search.sh"
FROZEN_VM_REPLAY="$FROZEN_SUPPORT_DIR/bonect_parallel_vm_replay.py"
FROZEN_REAL_VALIDATOR="$FROZEN_SUPPORT_DIR/bonect_real_validate_candidates.py"
FROZEN_WORKER_FREEZER="$FROZEN_SUPPORT_DIR/freeze_vm_replay_worker_config.py"
FROZEN_PLAN_SQL="$FROZEN_SUPPORT_DIR/binary_plan_tuning_hybenchxp_plan_workload.sql"
FROZEN_NONE_SETUP="$FROZEN_SUPPORT_DIR/hybenchxp_none_physio_setup.sql"
FROZEN_NONE_AP6="$FROZEN_SUPPORT_DIR/hybenchxp_none_ap6_physio_600_300.sql"
cp "$ROOT_DIR/shell/supervise_hybenchxp_vm_binary_search.sh" "$FROZEN_SUPERVISOR"
cp "$ROOT_DIR/shell/bonect_parallel_vm_replay.py" "$FROZEN_VM_REPLAY"
cp "$ROOT_DIR/shell/bonect_real_validate_candidates.py" "$FROZEN_REAL_VALIDATOR"
cp "$ROOT_DIR/shell/freeze_vm_replay_worker_config.py" "$FROZEN_WORKER_FREEZER"
cp "$ROOT_DIR/shell/binary_plan_tuning_hybenchxp_plan_workload.sql" "$FROZEN_PLAN_SQL"
cp "$ROOT_DIR/shell/hybenchxp_none_physio_setup.sql" "$FROZEN_NONE_SETUP"
cp "$ROOT_DIR/shell/hybenchxp_none_ap6_physio_600_300.sql" "$FROZEN_NONE_AP6"
jq \
    --arg strategy "$EXPERIMENT_STRATEGY" \
    --arg policy "$STRATEGY_POLICY" \
    --argjson topk "$STRATEGY_TOPK" \
    --arg plan_sql "$FROZEN_PLAN_SQL" \
    --arg none_setup_sql "$FROZEN_NONE_SETUP" \
    --arg none_ap6_sql "$FROZEN_NONE_AP6" \
    '.binary_plan_tuning.binary_search.experiment_strategy = $strategy
     | .binary_plan_tuning.binary_search.range_anchor_policy = $policy
     | .binary_plan_tuning.binary_search.max_candidates_per_round = $topk
     | .binary_plan_tuning.binary_search.full_bruteforce = false
     | .binary_plan_tuning.binary_search.disable_candidate_reduction = false
     | .binary_plan_tuning.binary_search.replay_candidate_pruning.enabled = false
     | .binary_plan_tuning.acceptance_policy.none_require_resource_up = true
     | .binary_plan_tuning.acceptance_policy.target_overrides.none.none_require_resource_up = true
     | .binary_plan_tuning.workload.plan_sql_file = $plan_sql
     | .binary_plan_tuning.validation.hybench_xp.target_overrides.none.setup_sql_files = [$none_setup_sql]
     | .binary_plan_tuning.validation.hybench_xp.target_overrides.none.statement_sql_files["AP-6"] = $none_ap6_sql' \
    "$CONFIG" >"$FROZEN_CONFIG"
cp "$ROOT_DIR/binary_plan_resource_tuner.py" "$FROZEN_TUNER"
cp "$ROOT_DIR/plan_resource_estimator.py" "$FROZEN_PLAN_ESTIMATOR"
cp "$ROOT_DIR/resource_feature_predictor.py" "$FROZEN_FEATURE_PREDICTOR"
cp "$ROOT_DIR/extract_plan_trees.py" "$FROZEN_PLAN_EXTRACTOR"
cp "$ROOT_DIR/bonect_interval_lineage.py" "$FROZEN_INTERVAL_LINEAGE"
cp "$ROOT_DIR/system_resource_monitor.py" "$FROZEN_RESOURCE_MONITOR"
cp "$ROOT_DIR/resource_utility.py" "$FROZEN_RESOURCE_UTILITY"
cp "$ROOT_DIR/shell/watch_vm_host_disk_conflicts.py" "$FROZEN_GUARD"
cp "$ROOT_DIR/shell/watch_vm_binary_search_best_so_far.py" "$FROZEN_MONITOR"
cp "$ROOT_DIR/shell/watch_hybenchxp_four_vm_recall_completion.py" "$FROZEN_COMPLETION_WATCHER"
cp "$ROOT_DIR/shell/audit_hybenchxp_four_vm_recall_completion.py" "$FROZEN_AUDIT"

FROZEN_WORKER_INPUT_DIR="$SNAPSHOT_DIR/vm_replay_inputs"
for target in "${TARGET_LIST[@]}"; do
    source_worker=$(jq -r --arg target "$target" \
        '.binary_plan_tuning.validation.vm_replay.worker_configs[$target] // empty' \
        "$FROZEN_CONFIG")
    if [[ -z "$source_worker" ]]; then
        echo "frozen worker config source is missing: target=$target" >&2
        exit 1
    fi
    frozen_worker_dir="$FROZEN_WORKER_INPUT_DIR/$target"
    python3 "$FROZEN_WORKER_FREEZER" \
        --project-root "$ROOT_DIR" \
        --source-worker "$source_worker" \
        --target "$target" \
        --output-dir "$frozen_worker_dir"
    frozen_worker_config="$frozen_worker_dir/worker_config.json"
    jq --arg target "$target" --arg path "$frozen_worker_config" \
        '.binary_plan_tuning.validation.vm_replay.worker_configs[$target] = $path' \
        "$FROZEN_CONFIG" >"$FROZEN_CONFIG.tmp"
    mv "$FROZEN_CONFIG.tmp" "$FROZEN_CONFIG"
done

if ! env \
    BINARY_PLAN_RESOURCE_TUNER_ROOT="$SNAPSHOT_DIR" \
    PYTHONPATH="$SNAPSHOT_DIR:$FROZEN_SUPPORT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -c \
        'import binary_plan_resource_tuner, bonect_parallel_vm_replay, bonect_real_validate_candidates'; then
    echo "frozen runtime import closure validation failed" >&2
    if grep -Fqx "run_root=$RUN_ROOT" "$EXCLUSIVE_LOCK_PATH" 2>/dev/null; then
        rm -f "$EXCLUSIVE_LOCK_PATH"
    fi
    exit 1
fi

FROZEN_VALIDATION_DIR="$SNAPSHOT_DIR/config_validation"
if ! env \
    BINARY_PLAN_RESOURCE_TUNER_ROOT="$ROOT_DIR" \
    PYTHONPATH="$ROOT_DIR:$ROOT_DIR/shell${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -c \
    'import binary_plan_resource_tuner, bonect_parallel_vm_replay, bonect_real_validate_candidates'; then
    echo "runtime import closure validation failed" >&2
    if grep -Fqx "run_root=$RUN_ROOT" "$EXCLUSIVE_LOCK_PATH" 2>/dev/null; then
        rm -f "$EXCLUSIVE_LOCK_PATH"
    fi
    exit 1
fi
for target in "${TARGET_LIST[@]}"; do
    if ! env \
        BINARY_PLAN_RESOURCE_TUNER_ROOT="$SNAPSHOT_DIR" \
        PYTHONPATH="$SNAPSHOT_DIR:$FROZEN_SUPPORT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
        python3 "$FROZEN_TUNER" \
        --config "$FROZEN_CONFIG" \
        --output-dir "$FROZEN_VALIDATION_DIR/$target" \
        validate-config --target "$target" --strict; then
        echo "frozen config validation failed: target=$target" >&2
        if grep -Fqx "run_root=$RUN_ROOT" "$EXCLUSIVE_LOCK_PATH" 2>/dev/null; then
            rm -f "$EXCLUSIVE_LOCK_PATH"
        fi
        exit 1
    fi
done
sha256sum \
    "$FROZEN_CONFIG" \
    "$FROZEN_TUNER" \
    "$FROZEN_PLAN_ESTIMATOR" \
    "$FROZEN_FEATURE_PREDICTOR" \
    "$FROZEN_PLAN_EXTRACTOR" \
    "$FROZEN_INTERVAL_LINEAGE" \
    "$FROZEN_RESOURCE_MONITOR" \
    "$FROZEN_RESOURCE_UTILITY" \
    "$FROZEN_GUARD" \
    "$FROZEN_MONITOR" \
    "$FROZEN_COMPLETION_WATCHER" \
    "$FROZEN_AUDIT" \
    "$FROZEN_SUPERVISOR" \
    "$FROZEN_VM_REPLAY" \
    "$FROZEN_REAL_VALIDATOR" \
    "$FROZEN_WORKER_FREEZER" \
    "$FROZEN_PLAN_SQL" \
    "$FROZEN_NONE_SETUP" \
    "$FROZEN_NONE_AP6" >"$SNAPSHOT_DIR/SHA256SUMS"
find "$FROZEN_WORKER_INPUT_DIR" -type f -print0 \
    | sort -z \
    | xargs -0 sha256sum >>"$SNAPSHOT_DIR/SHA256SUMS"

disk_device_number=$(<"/sys/class/block/$disk_device/dev")
default_vm_run_mins=$(jq -r '.binary_plan_tuning.validation.vm_replay.run_mins' "$FROZEN_CONFIG")
io_vm_run_mins=$(
    jq -r '.binary_plan_tuning.validation.vm_replay.run_mins_by_target.io //
        .binary_plan_tuning.validation.vm_replay.run_mins' "$FROZEN_CONFIG"
)
manifest_format='run_id=%s\nrun_root=%s\nsession=%s\nconfig=%s\nconfig_source=%s\n'
manifest_format+='tuner_script=%s\naudit_script=%s\nlaunch_snapshot_dir=%s\n'
manifest_format+='launch_snapshot_sha256s=%s\n'
manifest_format+='preflight_disk_device=%s\npreflight_read_mib_s=%s\n'
manifest_format+='preflight_write_mib_s=%s\npreflight_util_pct=%s\n'
manifest_format+='preflight_max_read_mib_s=%s\npreflight_max_write_mib_s=%s\n'
manifest_format+='preflight_max_util_pct=%s\npreflight_passed=true\n'
manifest_format+='host_guard_device=%s\nhost_guard_max_docker_mib_s=%s\n'
manifest_format+='host_guard_max_qemu_mib_s=%s\nhost_guard_allowed_qemu_guests=%s\n'
manifest_format+='host_guard_interval_sec=%s\ndefault_vm_run_mins=%s\nio_vm_run_mins=%s\n'
manifest_format+='exclusive_lock_path=%s\n'
manifest_format+='targets=%s\nfinal_stop_reasons=%s\n'
manifest_format+='experiment_strategy=%s\nstrategy_policy=%s\nstrategy_topk=%s\n'
printf "$manifest_format" \
    "$RUN_ID" "$RUN_ROOT" "$SESSION" "$FROZEN_CONFIG" "$CONFIG" \
    "$FROZEN_TUNER" "$FROZEN_AUDIT" "$SNAPSHOT_DIR" "$SNAPSHOT_DIR/SHA256SUMS" \
    "$disk_device" "$read_mib_s" "$write_mib_s" \
    "$util_pct" "$PREFLIGHT_MAX_READ_MIB_S" "$PREFLIGHT_MAX_WRITE_MIB_S" \
    "$PREFLIGHT_MAX_UTIL_PCT" "$disk_device_number" "$HOST_GUARD_MAX_DOCKER_MIB_S" \
    "$HOST_GUARD_MAX_QEMU_MIB_S" "$ALLOWED_QEMU_GUESTS" \
    "$HOST_GUARD_INTERVAL_SEC" "$default_vm_run_mins" "$io_vm_run_mins" \
    "$EXCLUSIVE_LOCK_PATH" "$TARGETS" "$FINAL_STOP_REASONS" \
    "$EXPERIMENT_STRATEGY" "$STRATEGY_POLICY" "$STRATEGY_TOPK" >"$RUN_ROOT/launch_manifest.txt"

guard_command=(
    python3
    "$FROZEN_GUARD"
    --interval-sec
    "$HOST_GUARD_INTERVAL_SEC"
    --max-docker-mib-s
    "$HOST_GUARD_MAX_DOCKER_MIB_S"
    --max-qemu-mib-s
    "$HOST_GUARD_MAX_QEMU_MIB_S"
    --allowed-qemu-guests
    "${ALLOWED_QEMU_GUEST_LIST[@]}"
    --device
    "$disk_device_number"
    --targets
    "${TARGET_LIST[@]}"
    --stop-reasons
    "${FINAL_STOP_REASON_LIST[@]}"
    --lock-path
    "$EXCLUSIVE_LOCK_PATH"
    "$RUN_ROOT"
    "$SESSION"
)
printf -v guard_shell '%q ' "${guard_command[@]}"
guard_shell="cd $(printf '%q' "$ROOT_DIR"); exec $guard_shell"
tmux new-session -d -s "$SESSION" -n host-guard "bash -lc $(printf '%q' "$guard_shell")"
tmux set-option -t "$SESSION" remain-on-exit on >/dev/null

guard_ready=false
for _ in {1..20}; do
    if [[ -s "$RUN_ROOT/host_disk_isolation_status.json" ]] && \
        jq -e '.event == "host_disk_guard_start" and .status == "ok" and (.conflicts | length == 0)' \
        "$RUN_ROOT/host_disk_isolation_status.json" >/dev/null; then
        guard_ready=true
        break
    fi
    if ! tmux has-session -t "$SESSION" 2>/dev/null; then
        break
    fi
    sleep 0.25
done
if [[ "$guard_ready" != "true" ]]; then
    echo "host disk guard did not establish a clean start record" >&2
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    if grep -Fqx "run_root=$RUN_ROOT" "$EXCLUSIVE_LOCK_PATH" 2>/dev/null; then
        rm -f "$EXCLUSIVE_LOCK_PATH"
    fi
    exit 1
fi

for target in "${TARGET_LIST[@]}"; do
    target_dir="$RUN_ROOT/$target"
    mkdir -p "$target_dir"
    args=(
        env
        "BINARY_PLAN_TERMINAL_STOP_REASONS=$FINAL_STOP_REASONS"
        "BINARY_PLAN_RESOURCE_TUNER_ROOT=$SNAPSHOT_DIR"
        "$FROZEN_SUPERVISOR"
        "$FROZEN_CONFIG"
        "$target_dir"
        "$target"
        "${HOSTS[$target]}"
        "$target_dir/controller.log"
        "$FROZEN_TUNER"
    )
    printf -v command '%q ' "${args[@]}"
    command="set -o pipefail; cd $(printf '%q' "$ROOT_DIR"); $command"
    tmux new-window -d -t "$SESSION" -n "$target" "bash -lc $(printf '%q' "$command")"
done

if [[ "$FINAL_STOP_REASONS" == "epsilon_converged" ]]; then
    monitor_command=(
        python3
        "$FROZEN_MONITOR"
        --interval-sec
        30
        "$RUN_ROOT"
    )
    printf -v monitor_shell '%q ' "${monitor_command[@]}"
    monitor_shell="cd $(printf '%q' "$ROOT_DIR"); exec $monitor_shell"
    tmux new-window -d -t "$SESSION" -n monitor "bash -lc $(printf '%q' "$monitor_shell")"
fi

if [[ ${#TARGET_LIST[@]} -eq 4 && "$FINAL_STOP_REASONS" == "epsilon_converged" ]]; then
    completion_audit_command=(
        python3
        "$FROZEN_COMPLETION_WATCHER"
        --interval-sec
        30
        --retry-sec
        60
        --audit-script
        "$FROZEN_AUDIT"
        "$RUN_ROOT"
    )
    printf -v completion_audit_shell '%q ' "${completion_audit_command[@]}"
    completion_audit_shell="cd $(printf '%q' "$ROOT_DIR"); exec $completion_audit_shell"
    tmux new-window -d -t "$SESSION" -n completion-audit \
        "bash -lc $(printf '%q' "$completion_audit_shell")"
fi

cat "$RUN_ROOT/launch_manifest.txt"
tmux list-windows -t "$SESSION"
launch_complete=true
trap - EXIT
