#!/usr/bin/env bash
set -euo pipefail

old_pid="3168775"
old_root="/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260812_algorithm_c_targethard_nontargetsoft_job5long2_clean_p2/job_memory"
new_tag="20260812_algorithm_c_job_nonblocking_lpt_clean_r1"
project_root="/home/anonymous/program/resource_adaptive"
python_bin="/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
launcher="$project_root/shell/launch_pg_algorithm_c_four_experience_sgd_20260809.py"
deadline_epoch="$(( $(date +%s) + 10800 ))"
lock_path="/tmp/restart_job_after_clean_stop_20260812.lock"

exec 9>"$lock_path"
if ! flock -n 9; then
    exit 0
fi

echo "waiting for JOB controller pid=$old_pid to reach a clean wave boundary"
while ps -p "$old_pid" >/dev/null 2>&1; do
    if (( $(date +%s) >= deadline_epoch )); then
        echo "timed out waiting for old JOB controller pid=$old_pid" >&2
        exit 1
    fi
    sleep 5
done

phase="$(jq -r '.phase // ""' "$old_root/calibration_state.json")"
if [[ "$phase" != "stopped_by_signal_clean" ]]; then
    echo "old JOB controller did not stop cleanly: phase=$phase" >&2
    exit 1
fi

if ps -ef | grep -F "$old_root" | grep -v -E 'grep|restart_job_after_clean_stop' >/dev/null; then
    echo "old JOB workload process remains after controller exit" >&2
    exit 1
fi

echo "old JOB lane stopped cleanly; launching repaired lane tag=$new_tag"
cd "$project_root"
exec "$python_bin" -B "$launcher" launch --tag "$new_tag" --scene job_memory
