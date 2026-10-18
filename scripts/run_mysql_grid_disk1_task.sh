#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 11 && $# -ne 12 ]]; then
  echo "usage: $0 RUN_KEY EXP_ID REPEAT_ID BENCHMARK BOTTLENECK CONTAINER PORT DATABASE CPU_CORES MEM_GB SCALE_FACTOR [DISK_BW_MB]" >&2
  exit 64
fi

run_key=$1
exp_id=$2
repeat_id=$3
benchmark=$4
bottleneck=$5
container=$6
port=$7
database=$8
cpu_cores=$9
mem_gb=${10}
scale_factor=${11}
disk_bw_mb=${12:-100}

output_root=/disk1/resource_adaptive_mysql_grid_20260718/results/mysql_grid_20260718
output=${output_root}/${run_key}
owner_label=resource-adaptive-mysql.owner=mysql-grid-20260718-disk1
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
runner=/home/anonymous/program/resource_adaptive_mysql/run_mysql_logical_grid.py

test ! -e "${output}"
container_pid=$(docker inspect --format '{{.State.Pid}}' "${container}")
test "${container_pid}" -gt 0

if [[ "${benchmark}" == tpcc ]]; then
  MYSQL_PWD=CHANGE_ME mysql --connect-timeout=5 \
    -h127.0.0.1 -P"${port}" -uroot "${database}" \
    -e 'SET GLOBAL max_connections=512; SET GLOBAL innodb_spin_wait_delay=6; SET GLOBAL innodb_thread_concurrency=2'
fi

exec "${python_bin}" -u "${runner}" \
  --exp-id "${exp_id}" \
  --repeat-id "${repeat_id}" \
  --benchmark "${benchmark}" \
  --bottleneck "${bottleneck}" \
  --host 127.0.0.1 \
  --port "${port}" \
  --pid "${container_pid}" \
  --database "${database}" \
  --user root \
  --password CHANGE_ME \
  --cpu-cores "${cpu_cores}" \
  --mem-gb "${mem_gb}" \
  --disk-bw-mb "${disk_bw_mb}" \
  --blkio-device 259:5 \
  --scale-factor "${scale_factor}" \
  --hybench-scale 10x \
  --output "${output}" \
  --container "${container}" \
  --required-label "${owner_label}"
