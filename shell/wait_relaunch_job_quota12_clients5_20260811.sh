#!/usr/bin/env bash
set -euo pipefail

old_pid=1132105
old_root=/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/20260811_algorithm_c_escape_jobfix_clean_j1/job_memory
run_base=/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809
new_tag=20260811_algorithm_c_job_clients5_quota12_clean_l1
new_root=${run_base}/${new_tag}
control_dir=${run_base}/control_waiters
control_log=${control_dir}/job_clients5_quota12_relaunch.log
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
launcher=/home/anonymous/program/resource_adaptive/shell/launch_pg_algorithm_c_four_experience_sgd_20260809.py

mkdir -p "${control_dir}"
exec >>"${control_log}" 2>&1

echo "$(date -Is) waiting_for_clean_job_exit pid=${old_pid}"
while kill -0 "${old_pid}" 2>/dev/null; do
    sleep 15
done

phase=$(jq -r '.phase // ""' "${old_root}/calibration_state.json")
if [[ "${phase}" != "stopped_by_signal_clean" ]]; then
    echo "$(date -Is) blocked_old_job_not_clean phase=${phase}"
    exit 1
fi

echo "$(date -Is) old_job_clean preparing=${new_root}"
if [[ ! -f "${new_root}/launch_manifest.json" ]]; then
    "${python_bin}" "${launcher}" prepare --tag "${new_tag}" --scene job_memory
fi
"${python_bin}" "${launcher}" launch --tag "${new_tag}" --scene job_memory
echo "$(date -Is) new_job_launched root=${new_root}"
