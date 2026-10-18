#!/usr/bin/env bash
set -euo pipefail

lane=/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpch_node04
root="$lane/experience_sgd/attempt45_tsgd_eta010_target5_v1"
config="$root/config/tpch_experience_table_all_subset_sgd.json"
log="$root/formal_dbqps.log"
pidfile="$root/optimizer.pid"
python_bin=/home/anonymous/recost_runtime_py311_24_11_1/bin/python3.11

test -x "$python_bin"
test -f "$config"
test ! -e "$pidfile"
if pgrep -f '[p]arameter_optimizer.py.*attempt45_tsgd_eta010_target5_v1' >/dev/null; then
    echo "TPC-H attempt45 T-SGD already active" >&2
    exit 4
fi

export OPTIMIZER_CONFIG="$config"
export PYTHONPATH="$root/code"
export PYTHONUNBUFFERED=1
unset LD_LIBRARY_PATH
nohup "$python_bin" -u "$root/code/parameter_optimizer.py" >"$log" 2>&1 &
pid=$!
printf '%s\n' "$pid" >"$pidfile"
echo "pid=$pid log=$log"
