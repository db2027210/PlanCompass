#!/usr/bin/env bash
set -u
ROOT=/home/anonymous/program/resource_adaptive_mysql/history/parallel24_20260721_v1/mysql/recost_gradient_descent
while true; do
  date --iso-8601=seconds
  timeout 10 docker ps --filter 'name=p24_mysql_recost_gradient_descent_' --format 'DB {{.Names}} | {{.Status}} | {{.Ports}}' || echo 'DB docker daemon query timeout (create/remove queue active)'
  ps -eo pid,etimes,cmd | grep -E 'parallel24_recost_worker.py|HyBenchFormalMain|FormalSQLWorkload|benchbase.jar' | grep -v grep || true
  for case_name in hybench_ap_memory job_memory tpch_memory hybench_xp_io; do
    state=$(find "$ROOT" -path "*parallel24_${case_name}_r1/run_state.json" -print -quit 2>/dev/null)
    if [[ -n "$state" ]]; then
      python3 -c 'import json,sys; x=json.load(open(sys.argv[1])); print(sys.argv[2], x.get("status"), x.get("stage"), "candidates="+str(len(x.get("candidates",[]))))' "$state" "$case_name" 2>/dev/null || true
    else
      echo "$case_name preparing"
    fi
  done
  sleep 60
done
