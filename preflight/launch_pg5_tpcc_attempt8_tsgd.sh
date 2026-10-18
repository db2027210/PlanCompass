#!/usr/bin/env bash
set -euo pipefail

root=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt8_tsgd_v1
config="$root/config/tpcc_experience_table_all_subset_sgd.json"
log="$root/formal_dbqps.log"
pidfile="$root/optimizer.pid"
python=/usr/bin/python3
lane_lock=/tmp/pg5_tpcc_59640_exclusive.lock
optimizer_lock=/tmp/resource_adaptive_bonect_parameter_optimizer_pg59640_disk2_attempt8.lock

test -f "$config"
if [[ -e "$pidfile" ]]; then
    old_pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
        echo "TPC-C attempt8 T-SGD already active: pid=$old_pid" >&2
        exit 4
    fi
    rm -f -- "$pidfile"
fi

if ! "$python" -c 'import joblib, psycopg2, sklearn' >/dev/null 2>&1; then
    echo "TPC-C optimizer Python lacks joblib/psycopg2/sklearn: $python" >&2
    exit 5
fi

PG5_TPCC_LANE_LOCK_PATH="$lane_lock" \
    /home/anonymous/program/resource_adaptive/shell/check_pg5_tpcc_lane_idle.sh

export OPTIMIZER_CONFIG="$config"
export PYTHONUNBUFFERED=1
export BONECT_OPTIMIZER_LOCK_PATH="$optimizer_lock"
nohup flock -n -E 73 "$lane_lock" "$python" -u \
    /home/anonymous/program/resource_adaptive/parameter_optimizer.py \
    >>"$log" 2>&1 &
pid=$!
printf '%s\n' "$pid" >"$pidfile"
echo "pid=$pid log=$log"
