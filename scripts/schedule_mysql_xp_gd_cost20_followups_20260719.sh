#!/usr/bin/env bash
# Schedule only the remaining failed B9 logical runs onto goal-owned XP
# workers after their current formal runs have fully released the controller.
set -euo pipefail

workspace=/home/anonymous/program/resource_adaptive_mysql
cd "$workspace"
python_bin=/home/anonymous/miniconda3/envs/py313/bin/python
a_status=runtime_status/formal_dynamic_retry209_a30_x1t1_pool272_cost20_cal095_auto5/matrix_status.json
b2_status=runtime_status/formal_dynamic_retry208_b9r2_x1t1_pool272_cost25_cal090_auto6/matrix_status.json

completed() {
  [[ -f "$1" ]] && jq -e '.runs[] | select(.status == "completed")' "$1" >/dev/null 2>&1
}

passed() {
  [[ -f "$1" ]] && jq -e '.runs[] | select(.evaluation.passed == true)' "$1" >/dev/null 2>&1
}

launch() {
  local session=$1 worker_config=$2 container=$3 controller=$4 output=$5 run_id=$6 calibration=$7 profile=$8
  docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null | grep -qx true
  ! pgrep -f "$controller" >/dev/null 2>&1
  ! tmux has-session -t "$session" 2>/dev/null
  tmux new-session -d -s "$session" \
    "cd $workspace && exec $python_bin run_mysql_gd_matrix.py \
      --workers-json $worker_config --output-root $workspace/$output \
      --include-run $run_id --bottleneck-initial-util $calibration \
      --workload-profile stabilized --parameter-profile $profile \
      --measurement-repeats 5 --calibration-repeats 7 \
      --hybench-xap-clients 1 --hybench-xtp-clients 1 --hybench-ap-query-sequence 9 \
      --optimizer-max-iter 1"
}

b1_started=false
b2_retry_started=false
if tmux has-session -t ra_mysql_b9r1_retry210_20260719 2>/dev/null; then
  b1_started=true
fi
if tmux has-session -t ra_mysql_b9r2_retry211_20260719 2>/dev/null; then
  b2_retry_started=true
fi
while :; do
  if ! $b1_started && completed "$b2_status"; then
    if launch ra_mysql_b9r1_retry210_20260719 \
        config/mysql_gd_worker_hybench_xp_auto6_20260719.json ra_mysql_gd_hybench_xp_auto6 \
        'run_mysql_gd_matrix.py.*mysql_gd_worker_hybench_xp_auto6_20260719.json' \
        runtime_status/formal_dynamic_retry210_b9r1_x1t1_pool272_cost20_cal097_auto6 B9-r1 \
        0.97 xp_io_q9_pool272_cost20_topk; then
      b1_started=true
    fi
  fi
  if ! $b2_retry_started && completed "$a_status" && completed "$b2_status" && ! passed "$b2_status"; then
    if launch ra_mysql_b9r2_retry211_20260719 \
        config/mysql_gd_worker_hybench_xp_auto5_20260719.json ra_mysql_gd_hybench_xp_auto5 \
        'run_mysql_gd_matrix.py.*mysql_gd_worker_hybench_xp_auto5_20260719.json' \
        runtime_status/formal_dynamic_retry211_b9r2_x1t1_pool272_cost25_cal096_auto5 B9-r2 \
        0.96 xp_io_q9_pool272_cost25_topk; then
      b2_retry_started=true
    fi
  fi
  if $b1_started && ($b2_retry_started || passed "$b2_status"); then
    exit 0
  fi
  sleep 30
done
