#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/20260816_algorithm_a_full_factorial_globalrank_labels_clean_r5
watchdog_log=${campaign_root}/cron_supervisor.log
lock_path=/tmp/resource_adaptive_pg_algorithm_a_r5_cron_watch_v2.lock

exec 9>"${lock_path}"
flock -n 9 || exit 0

start_lane_if_needed() {
    local lane=$1
    local state_dir=$2
    local state_path=${campaign_root}/${state_dir}/algorithm_a_state.json
    local pause_path=${campaign_root}/${state_dir}/.algorithm_a_watchdog_pause
    local phase accepted target pattern controller_pattern

    # A round-boundary execution-domain transition owns the lane while this
    # sentinel exists.  The Python watchdog already implements the same gate,
    # but crontab invokes this shell wrapper directly.
    [[ -f ${pause_path} ]] && return 0
    [[ -f ${state_path} ]] || return 0
    IFS=$'\t' read -r phase accepted target < <(
        jq -r '[.phase // "missing", .accepted // 0, .accepted_target // 7] | @tsv' \
            "${state_path}"
    )
    case "${phase}" in
        complete|algorithm_a_nonconvergence_clean|algorithm_a_technical_failure_stopped_clean|all_champions_semantic_stability_failed_clean|clean_restore_technical_failure)
            return 0
            ;;
    esac
    if (( accepted >= target )) && [[ ${phase} == complete ]]; then
        return 0
    fi
    pattern="${project_root}/shell/supervise_pg_algorithm_a_r5_lane.sh ${lane}"
    controller_pattern="run_pg_algorithm_a_scene.py --config ${campaign_root}/${state_dir}/optimizer_config.json"
    if pgrep -f -- "${pattern}" >/dev/null \
        || pgrep -f -- "${controller_pattern}" >/dev/null; then
        return 0
    fi
    printf '%s lane=%s event=cron_supervisor_start phase=%s accepted=%s/%s\n' \
        "$(date --iso-8601=seconds)" "${lane}" "${phase}" "${accepted}" "${target}" \
        >> "${watchdog_log}"
    setsid --fork nohup \
        "${project_root}/shell/supervise_pg_algorithm_a_r5_lane.sh" "${lane}" \
        >> "${campaign_root}/${state_dir}/cron_bootstrap.log" 2>&1 < /dev/null 9>&-
}

start_lane_if_needed job job_memory
start_lane_if_needed tpch tpch_memory
start_lane_if_needed xp_cpu xp_cpu
