#!/usr/bin/env bash
set -euo pipefail

project_root="/home/anonymous/program/resource_adaptive"
python_bin="/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
freeze_script="$project_root/shell/freeze_algorithm_c_calibration.py"
release_root="/disk1/resource_adaptive/pg_algorithm_c_calibration_releases_v1"
deadline_epoch="$(( $(date +%s) + 86400 ))"
lock_path="/tmp/freeze_current_algorithm_c_calibrations_20260812.lock"

exec 9>"$lock_path"
if ! flock -n 9; then
    exit 0
fi

declare -A scene_dirs=(
    [job_memory]="/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260812_algorithm_c_job_nonblocking_lpt_clean_r1/job_memory"
    [tpch_memory]="/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260812_algorithm_c_tpch_timeout7200_clean_r1/tpch_memory"
    [xp_cpu]="/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260812_algorithm_c_targethard_nontargetsoft_job5long2_clean_p2/xp_cpu"
    [xp_io]="/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260812_algorithm_c_targethard_nontargetsoft_job5long2_clean_p2/xp_io"
)
declare -A frozen=()

echo "waiting to publish four verified Algorithm C calibration releases"
while (( ${#frozen[@]} < ${#scene_dirs[@]} )); do
    if (( $(date +%s) >= deadline_epoch )); then
        echo "timed out before all calibration releases became ready" >&2
        exit 1
    fi
    for scene in job_memory tpch_memory xp_cpu xp_io; do
        if [[ "${frozen[$scene]:-}" == "yes" ]]; then
            continue
        fi
        source_dir="${scene_dirs[$scene]}"
        output_path="/tmp/frozen_algorithm_c_${scene}_20260812.json"
        error_path="/tmp/frozen_algorithm_c_${scene}_20260812.err"
        if "$python_bin" -B "$freeze_script" "$source_dir" \
            --release-root "$release_root" >"$output_path" 2>"$error_path"; then
            manifest_path="$(jq -r '.manifest_path' "$output_path")"
            frozen[$scene]="yes"
            echo "published scene=$scene manifest=$manifest_path"
        fi
    done
    if (( ${#frozen[@]} < ${#scene_dirs[@]} )); then
        sleep 30
    fi
done
echo "all four calibration releases published"
