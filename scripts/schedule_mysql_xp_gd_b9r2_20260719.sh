#!/usr/bin/env bash
# Dispatch the last XP logical run to the first goal-owned worker that becomes
# free.  All controller state and outputs stay in the user-owned workspace.
set -euo pipefail

workspace=/home/anonymous/program/resource_adaptive_mysql
cd "$workspace"

a_status=runtime_status/formal_dynamic_retry206_a30_x1t1_pool272_cost25_cal090/matrix_status.json
b_status=runtime_status/formal_dynamic_retry207_b9r1_x1t1_pool272_cost25_cal097/matrix_status.json
python_bin=/home/anonymous/miniconda3/envs/py313/bin/python

completed() {
  local status_file=$1
  [[ -f "$status_file" ]] && jq -e '.runs[] | select(.status == "completed")' "$status_file" >/dev/null 2>&1
}

dispatch() {
  local worker_config=$1
  local container_name=$2
  local controller_pattern=$3
  local calibration=$4
  local output_root=$5

  docker inspect --format '{{.State.Running}}' "$container_name" 2>/dev/null | grep -qx true
  ! pgrep -f "$controller_pattern" >/dev/null 2>&1
  tmux new-session -d -s ra_mysql_b9r2_retry208_20260719 \
    "cd $workspace && exec $python_bin run_mysql_gd_matrix.py \
      --workers-json $worker_config \
      --output-root $workspace/$output_root \
      --include-run B9-r2 --bottleneck-initial-util $calibration \
      --workload-profile stabilized --parameter-profile xp_io_q9_pool272_cost25_topk \
      --measurement-repeats 5 --calibration-repeats 7 \
      --hybench-xap-clients 1 --hybench-xtp-clients 1 --hybench-ap-query-sequence 9 \
      --optimizer-max-iter 1"
}

while :; do
  # Ordering is dynamic: the first completed worker observed receives the run.
  if completed "$a_status"; then
    if dispatch config/mysql_gd_worker_hybench_xp_auto6_20260719.json \
        ra_mysql_gd_hybench_xp_auto6 \
        'run_mysql_gd_matrix.py.*mysql_gd_worker_hybench_xp_auto6_20260719.json' \
        0.90 runtime_status/formal_dynamic_retry208_b9r2_x1t1_pool272_cost25_cal090_auto6; then
      exit 0
    fi
  fi
  if completed "$b_status"; then
    if dispatch config/mysql_gd_worker_hybench_xp_auto5_20260719.json \
        ra_mysql_gd_hybench_xp_auto5 \
        'run_mysql_gd_matrix.py.*mysql_gd_worker_hybench_xp_auto5_20260719.json' \
        0.97 runtime_status/formal_dynamic_retry208_b9r2_x1t1_pool272_cost25_cal097_auto5; then
      exit 0
    fi
  fi
  sleep 30
done
