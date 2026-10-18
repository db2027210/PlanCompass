#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
campaign_root=/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/20260816_algorithm_a_full_factorial_globalrank_labels_clean_r5
lane_dir=${campaign_root}/tpch_memory
job_state=${campaign_root}/job_memory/algorithm_a_state.json
xp_cpu_state=${campaign_root}/xp_cpu/algorithm_a_state.json
xp_io_state=${campaign_root}/xp_io/algorithm_a_state.json
transition_dir=${lane_dir}/execution_domain_transitions
transition_id=tpch_workers2_to8_after_job_xpcpu_xpio_complete_20260820
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
    local required_path=$1
    if [[ ! -f ${required_path} ]]; then
        log_event "event=fail_closed reason=missing_file path=${required_path}"
        exit 2
    fi
}

canonical_json_sha256() {
    "${python_bin}" -c \
        "import hashlib,json,sys; value=json.load(open(sys.argv[1], encoding='utf-8')); payload=json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'); print(hashlib.sha256(payload).hexdigest())" \
        "$1"
}

lane_complete() {
    local completed_state=$1
    [[ $(jq -r '.accepted // 0' "${completed_state}") -ge 7 ]] \
        && [[ $(jq -r '.phase // "missing"' "${completed_state}") == complete ]]
}

release_completed_pg_container() {
    local container_name=$1
    if [[ ${container_name} == *mysql* ]]; then
        log_event "event=fail_closed reason=mysql_identity_refused container=${container_name}"
        exit 10
    fi
    local status
    status=$(timeout 30 docker inspect --format '{{.State.Status}}' "${container_name}")
    if [[ ${status} == running ]]; then
        timeout 180 docker stop --time 120 "${container_name}" >> "${watch_log}"
        log_event "event=released_pg_container_stopped container=${container_name}"
    elif [[ ${status} == exited || ${status} == created ]]; then
        log_event "event=released_pg_container_already_idle container=${container_name} status=${status}"
    else
        log_event "event=fail_closed reason=unexpected_released_container_status container=${container_name} status=${status}"
        exit 11
    fi
}

provision_pair() {
    local first=$1
    local second=$2
    local first_log=${transition_dir}/${transition_id}_provision_w${first}.log
    local second_log=${transition_dir}/${transition_id}_provision_w${second}.log
    log_event "event=provision_pair_started workers=w${first},w${second}"
    "${project_root}/shell/provision_pg_algorithm_a_replay_farm_worker.sh" \
        tpch "${first}" > "${first_log}" 2>&1 &
    local first_pid=$!
    "${project_root}/shell/provision_pg_algorithm_a_replay_farm_worker.sh" \
        tpch "${second}" > "${second_log}" 2>&1 &
    local second_pid=$!
    set +e
    wait "${first_pid}"
    local first_rc=$?
    wait "${second_pid}"
    local second_rc=$?
    set -e
    if (( first_rc != 0 || second_rc != 0 )); then
        log_event "event=fail_closed reason=provision_pair_failed workers=w${first},w${second} rc=${first_rc},${second_rc}"
        exit 12
    fi
    log_event "event=provision_pair_complete workers=w${first},w${second}"
}

for required in \
    "${source_config}" \
    "${staged_config}" \
    "${staged_request}" \
    "${manifest}" \
    "${live_config}" \
    "${stop_request}" \
    "${watchdog_pause}" \
    "${state_path}" \
    "${job_state}" \
    "${xp_cpu_state}" \
    "${xp_io_state}"; do
    require_file "${required}"
done

expected_source_sha=$(jq -r '.source_config_sha256' "${manifest}")
expected_target_sha=$(jq -r '.output_config_sha256' "${manifest}")
if [[ $(canonical_json_sha256 "${source_config}") != "${expected_source_sha}" ]]; then
    log_event "event=fail_closed reason=staged_source_sha_mismatch"
    exit 3
fi
if [[ $(canonical_json_sha256 "${staged_config}") != "${expected_target_sha}" ]]; then
    log_event "event=fail_closed reason=staged_target_sha_mismatch"
    exit 3
fi

if ! lane_complete "${xp_cpu_state}" || ! lane_complete "${xp_io_state}"; then
    log_event "event=fail_closed reason=xp_authority_not_complete"
    exit 4
fi

log_event "event=wait_started target_completed_round=4 target_workers=8 requires_job_complete=true"
while true; do
    tpch_phase=$(jq -r '.phase // "missing"' "${state_path}")
    tpch_accepted=$(jq -r '.accepted // 0' "${state_path}")
    tpch_checkpoint=$(jq -r '.algorithm_a_round_checkpoint // empty' "${state_path}")
    if lane_complete "${job_state}" \
        && (( tpch_accepted >= 4 )) \
        && [[ ${tpch_phase} == stopped_by_signal_clean ]] \
        && [[ -z ${tpch_checkpoint} ]]; then
        break
    fi
    case "${tpch_phase}" in
        complete|algorithm_a_nonconvergence_clean|algorithm_a_technical_failure_stopped_clean)
            log_event "event=fail_closed reason=unexpected_tpch_terminal_phase phase=${tpch_phase} accepted=${tpch_accepted}"
            exit 5
            ;;
    esac
    sleep 30
done
log_event "event=round4_and_job_clean_boundary_observed tpch_accepted=${tpch_accepted}"

mapfile -t supervisor_pids < <(
    pgrep -f "${project_root}/shell/supervise_pg_algorithm_a_r5_lane.sh tpch" || true
)
for supervisor_pid in "${supervisor_pids[@]}"; do
    if [[ ${supervisor_pid} != "$$" ]]; then
        kill -TERM "${supervisor_pid}" 2>/dev/null || true
        log_event "event=old_supervisor_term pid=${supervisor_pid}"
    fi
done

mapfile -t controller_pids < <(
    pgrep -f "run_pg_algorithm_a_scene.py --config ${live_config}" || true
)
for controller_pid in "${controller_pids[@]}"; do
    kill -TERM "${controller_pid}" 2>/dev/null || true
    log_event "event=old_controller_term pid=${controller_pid}"
done

# A clean stop can recreate two SF5 databases before the controller exits.
# A controller that was already in startup restore can require a second clean
# receipt after SIGTERM, so wait up to twenty minutes while
# remaining fail-closed: no worker provisioning starts until the old process is gone.
for _attempt in $(seq 1 600); do
    if ! pgrep -f "run_pg_algorithm_a_scene.py --config ${live_config}" >/dev/null; then
        break
    fi
    if (( _attempt % 30 == 0 )); then
        log_event "event=old_controller_clean_exit_wait attempts=${_attempt}"
    fi
    sleep 2
done
if pgrep -f "run_pg_algorithm_a_scene.py --config ${live_config}" >/dev/null; then
    log_event "event=fail_closed reason=old_controller_still_running"
    exit 6
fi

tpch_phase=$(jq -r '.phase // "missing"' "${state_path}")
tpch_accepted=$(jq -r '.accepted // 0' "${state_path}")
if [[ ${tpch_phase} != stopped_by_signal_clean ]] || (( tpch_accepted < 4 )); then
    log_event "event=fail_closed reason=boundary_state_changed phase=${tpch_phase} accepted=${tpch_accepted}"
    exit 7
fi
if ! lane_complete "${job_state}"; then
    log_event "event=fail_closed reason=job_completion_changed"
    exit 7
fi
if [[ $(canonical_json_sha256 "${live_config}") != "${expected_source_sha}" ]]; then
    log_event "event=fail_closed reason=live_source_sha_mismatch"
    exit 8
fi

for released_container in \
    ra_fullgrid_hybench_cpu_20260718_w1 \
    ra_fullgrid_hybench_cpu_20260718_w2 \
    ra_fullgrid_hybench_none_20260718_w1 \
    ra_fullgrid_hybench_none_20260718_w2 \
    ra_fullgrid_generic_mem_20260718_w1 \
    ra_fullgrid_generic_mem_20260718_w2; do
    release_completed_pg_container "${released_container}"
done

provision_pair 3 4
provision_pair 5 6
provision_pair 7 8

activation_epoch=$(date +%s)
cp -p "${live_config}" \
    "${transition_dir}/${transition_id}_live_config_before_${activation_epoch}.json"
cp -p "${staged_config}" "${live_config}"
cp -p "${staged_request}" "${live_request}"
activated_sha=$(canonical_json_sha256 "${live_config}")
if [[ ${activated_sha} != "${expected_target_sha}" ]]; then
    log_event "event=fail_closed reason=activated_config_sha_mismatch"
    exit 9
fi

mv "${stop_request}" \
    "${transition_dir}/${transition_id}_fulfilled_stop_request_${activation_epoch}.json"
log_event "event=eight_worker_config_activated config_sha256=${activated_sha}"

nohup setsid "${project_root}/shell/supervise_pg_algorithm_a_r5_lane.sh" tpch \
    >> "${lane_dir}/supervisor-bootstrap-tpch8.log" 2>&1 < /dev/null &
new_supervisor_pid=$!
sleep 5
if ! kill -0 "${new_supervisor_pid}" 2>/dev/null; then
    log_event "event=fail_closed reason=new_supervisor_rapid_exit pid=${new_supervisor_pid}"
    exit 13
fi

mv "${watchdog_pause}" \
    "${transition_dir}/${transition_id}_fulfilled_watchdog_pause_${activation_epoch}.json"
log_event "event=activation_complete supervisor_pid=${new_supervisor_pid} target_workers=8"
