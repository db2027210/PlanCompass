#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
campaign_root=/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/20260816_algorithm_a_full_factorial_globalrank_labels_clean_r5
lane_dir=${campaign_root}/tpch_memory
transition_dir=${lane_dir}/execution_domain_transitions
transition_id=tpch_workers2_to4_after_xpcpu_complete_20260820
source_config=${transition_dir}/${transition_id}_source_config.json
staged_config=${transition_dir}/${transition_id}_staged_config.json
staged_request=${transition_dir}/${transition_id}_staged_request.json
manifest=${transition_dir}/${transition_id}_staged_manifest.json
live_config=${lane_dir}/optimizer_config.json
live_request=${lane_dir}/algorithm_a_worker_cohort_transition.json
stop_request=${lane_dir}/algorithm_a_stop_after_round.json
watchdog_pause=${lane_dir}/.algorithm_a_watchdog_pause
state_path=${lane_dir}/algorithm_a_state.json
watch_log=${transition_dir}/${transition_id}_activation.log

log_event() {
    printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" >> "${watch_log}"
}

require_file() {
    local path=$1
    if [[ ! -f ${path} ]]; then
        log_event "event=fail_closed reason=missing_file path=${path}"
        exit 2
    fi
}

canonical_json_sha256() {
    "${python_bin}" -c \
        "import hashlib,json,sys; value=json.load(open(sys.argv[1], encoding='utf-8')); payload=json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'); print(hashlib.sha256(payload).hexdigest())" \
        "$1"
}

require_file "${source_config}"
require_file "${staged_config}"
require_file "${staged_request}"
require_file "${manifest}"
require_file "${live_config}"
require_file "${stop_request}"
require_file "${watchdog_pause}"
require_file "${state_path}"

expected_source_sha=$(jq -r '.source_config_sha256' "${manifest}")
expected_target_sha=$(jq -r '.output_config_sha256' "${manifest}")
actual_source_sha=$(canonical_json_sha256 "${source_config}")
actual_staged_sha=$(canonical_json_sha256 "${staged_config}")
if [[ ${actual_source_sha} != "${expected_source_sha}" ]]; then
    log_event "event=fail_closed reason=staged_source_sha_mismatch"
    exit 3
fi
if [[ ${actual_staged_sha} != "${expected_target_sha}" ]]; then
    log_event "event=fail_closed reason=staged_target_sha_mismatch"
    exit 3
fi

log_event "event=wait_started target_completed_round=4 target_workers=4"
while true; do
    phase=$(jq -r '.phase // "missing"' "${state_path}")
    accepted=$(jq -r '.accepted // 0' "${state_path}")
    checkpoint=$(jq -r '.algorithm_a_round_checkpoint // empty' "${state_path}")
    if (( accepted >= 4 )) \
        && [[ ${phase} == stopped_by_signal_clean ]] \
        && [[ -z ${checkpoint} ]]; then
        break
    fi
    case "${phase}" in
        complete|algorithm_a_nonconvergence_clean|algorithm_a_technical_failure_stopped_clean)
            log_event "event=fail_closed reason=unexpected_terminal_phase phase=${phase} accepted=${accepted}"
            exit 4
            ;;
    esac
    sleep 30
done
log_event "event=round4_clean_boundary_observed accepted=${accepted}"

# Stop only this lane's supervisor.  The controller sees the persistent
# round-boundary request and exits cleanly; no SIGKILL is used.
mapfile -t supervisor_pids < <(
    pgrep -f "${project_root}/shell/supervise_pg_algorithm_a_r5_lane.sh tpch" || true
)
for supervisor_pid in "${supervisor_pids[@]}"; do
    if [[ ${supervisor_pid} != "$$" ]]; then
        kill -TERM "${supervisor_pid}" 2>/dev/null || true
        log_event "event=old_supervisor_term pid=${supervisor_pid}"
    fi
done

for _attempt in $(seq 1 60); do
    if ! pgrep -f "run_pg_algorithm_a_scene.py --config ${live_config}" >/dev/null; then
        break
    fi
    sleep 2
done
if pgrep -f "run_pg_algorithm_a_scene.py --config ${live_config}" >/dev/null; then
    log_event "event=fail_closed reason=old_controller_still_running"
    exit 5
fi

phase=$(jq -r '.phase // "missing"' "${state_path}")
accepted=$(jq -r '.accepted // 0' "${state_path}")
if [[ ${phase} != stopped_by_signal_clean ]] || (( accepted < 4 )); then
    log_event "event=fail_closed reason=boundary_state_changed phase=${phase} accepted=${accepted}"
    exit 6
fi

live_sha=$(canonical_json_sha256 "${live_config}")
if [[ ${live_sha} != "${expected_source_sha}" ]]; then
    log_event "event=fail_closed reason=live_source_sha_mismatch actual=${live_sha} expected=${expected_source_sha}"
    exit 7
fi

log_event "event=provision_started worker=w3"
"${project_root}/shell/provision_pg_algorithm_a_replay_farm_worker.sh" tpch 3 \
    >> "${watch_log}" 2>&1
log_event "event=provision_complete worker=w3"
log_event "event=provision_started worker=w4"
"${project_root}/shell/provision_pg_algorithm_a_replay_farm_worker.sh" tpch 4 \
    >> "${watch_log}" 2>&1
log_event "event=provision_complete worker=w4"

activation_epoch=$(date +%s)
cp -p "${live_config}" \
    "${transition_dir}/${transition_id}_live_config_before_${activation_epoch}.json"
cp -p "${staged_config}" "${live_config}"
cp -p "${staged_request}" "${live_request}"
activated_sha=$(canonical_json_sha256 "${live_config}")
if [[ ${activated_sha} != "${expected_target_sha}" ]]; then
    log_event "event=fail_closed reason=activated_config_sha_mismatch"
    exit 8
fi

mv "${stop_request}" \
    "${transition_dir}/${transition_id}_fulfilled_stop_request_${activation_epoch}.json"
log_event "event=four_worker_config_activated config_sha256=${activated_sha}"

nohup setsid "${project_root}/shell/supervise_pg_algorithm_a_r5_lane.sh" tpch \
    >> "${lane_dir}/supervisor-bootstrap-tpch4.log" 2>&1 < /dev/null &
new_supervisor_pid=$!
sleep 5
if ! kill -0 "${new_supervisor_pid}" 2>/dev/null; then
    log_event "event=fail_closed reason=new_supervisor_rapid_exit pid=${new_supervisor_pid}"
    exit 9
fi

mv "${watchdog_pause}" \
    "${transition_dir}/${transition_id}_fulfilled_watchdog_pause_${activation_epoch}.json"
log_event "event=activation_complete supervisor_pid=${new_supervisor_pid} target_workers=4"
