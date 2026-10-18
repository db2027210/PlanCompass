#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
campaign_root=/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/20260816_algorithm_a_full_factorial_globalrank_labels_clean_r5
lane=${1:?usage: supervise_pg_algorithm_a_r5_lane.sh LANE}

case "${lane}" in
    job)
        output_dir=${campaign_root}/job_memory
        ;;
    tpch)
        output_dir=${campaign_root}/tpch_memory
        ;;
    xp_cpu)
        output_dir=${campaign_root}/xp_cpu
        ;;
    *)
        echo "unsupported supervised lane: ${lane}" >&2
        exit 2
        ;;
esac

state_path=${output_dir}/algorithm_a_state.json
supervisor_log=${output_dir}/supervisor.log
rapid_exit_count=0
stop_requested=0

request_stop() {
    stop_requested=1
}

trap request_stop TERM INT

run_lane() {
    if [[ ${lane} == job ]]; then
        worker_count=$(
            jq -r \
                '.binary_plan_tuning.validation.docker_workers.workers | length' \
                "${output_dir}/optimizer_config.json"
        )
        case "${worker_count}" in
            2)
                "${project_root}/shell/run_pg_algorithm_a_full_factorial_r5_lane.sh" job
                ;;
            4)
                "${project_root}/shell/run_pg_algorithm_a_full_factorial_r5_job4_lane.sh"
                ;;
            *)
                printf '%s lane=job event=unsupported_worker_count count=%s\n' \
                    "$(date --iso-8601=seconds)" "${worker_count}" \
                    >> "${supervisor_log}"
                return 64
                ;;
        esac
    elif [[ ${lane} == tpch ]]; then
        worker_count=$(
            jq -r \
                '.binary_plan_tuning.validation.docker_workers.workers | length' \
                "${output_dir}/optimizer_config.json"
        )
        case "${worker_count}" in
            2)
                "${project_root}/shell/run_pg_algorithm_a_full_factorial_r5_lane.sh" tpch
                ;;
            4)
                "${project_root}/shell/run_pg_algorithm_a_full_factorial_r5_tpch4_lane.sh"
                ;;
            8)
                "${project_root}/shell/run_pg_algorithm_a_full_factorial_r5_tpch8_lane.sh"
                ;;
            *)
                printf '%s lane=tpch event=unsupported_worker_count count=%s\n' \
                    "$(date --iso-8601=seconds)" "${worker_count}" \
                    >> "${supervisor_log}"
                return 64
                ;;
        esac
    else
        "${project_root}/shell/run_pg_algorithm_a_full_factorial_r5_lane.sh" "${lane}"
    fi
}

while true; do
    start_epoch=$(date +%s)
    printf '%s lane=%s event=controller_start\n' "$(date --iso-8601=seconds)" "${lane}" \
        >> "${supervisor_log}"
    set +e
    run_lane
    rc=$?
    set -e
    end_epoch=$(date +%s)
    elapsed=$((end_epoch - start_epoch))
    phase=$(jq -r '.phase // "missing"' "${state_path}" 2>/dev/null || echo unreadable)
    accepted=$(jq -r '.accepted // 0' "${state_path}" 2>/dev/null || echo 0)
    printf '%s lane=%s event=controller_exit rc=%s elapsed_s=%s phase=%s accepted=%s\n' \
        "$(date --iso-8601=seconds)" "${lane}" "${rc}" "${elapsed}" "${phase}" \
        "${accepted}" >> "${supervisor_log}"

    if (( stop_requested )); then
        printf '%s lane=%s event=supervisor_stop_requested_after_controller_exit\n' \
            "$(date --iso-8601=seconds)" "${lane}" >> "${supervisor_log}"
        exit 0
    fi

    case "${phase}" in
        complete)
            exit 0
            ;;
        algorithm_a_nonconvergence_clean|algorithm_a_technical_failure_stopped_clean|all_champions_semantic_stability_failed_clean)
            exit "${rc}"
            ;;
        stopped_by_signal_clean|signal_stop_pending_clean_restore|formal_search|fresh_parallel_baseline|execution_domain_transition_baseline_pending|execution_domain_transition_technical_retry|algorithm_a_full_factorial_real_replay|algorithm_a_incomplete_round_technical|offline_algorithm_a_full_factorial)
            ;;
        *)
            printf '%s lane=%s event=supervisor_fail_closed phase=%s\n' \
                "$(date --iso-8601=seconds)" "${lane}" "${phase}" \
                >> "${supervisor_log}"
            exit "${rc}"
            ;;
    esac

    if (( elapsed < 30 )); then
        rapid_exit_count=$((rapid_exit_count + 1))
    else
        rapid_exit_count=0
    fi
    if (( rapid_exit_count >= 3 )); then
        printf '%s lane=%s event=supervisor_rapid_exit_guard count=%s\n' \
            "$(date --iso-8601=seconds)" "${lane}" "${rapid_exit_count}" \
            >> "${supervisor_log}"
        exit 75
    fi
    sleep 5
    if (( stop_requested )); then
        printf '%s lane=%s event=supervisor_stop_requested_during_backoff\n' \
            "$(date --iso-8601=seconds)" "${lane}" >> "${supervisor_log}"
        exit 0
    fi
done
