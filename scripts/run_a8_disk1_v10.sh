#!/usr/bin/env bash
set -euo pipefail

root=/disk1/resource_adaptive_mysql_grid_20260718/results/mysql_grid_20260718
prewarm=${root}/_pilots/A8-r1-prewarm-v10
python_bin=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python

test ! -e "${prewarm}"
test ! -e "${root}/A8-r1"

"${python_bin}" -u /home/anonymous/program/resource_adaptive_mysql/mysql_benchmark_command.py \
  --benchmark job \
  --host 127.0.0.1 \
  --port 13412 \
  --database imdb_job \
  --user root \
  --password CHANGE_ME \
  --output-dir "${prewarm}" \
  --duration-sec 60 \
  --clients 2 \
  --scale-factor 1 \
  --job-query-files 1a.sql,2a.sql,3a.sql,4a.sql,5a.sql,10a.sql

exec /home/anonymous/program/resource_adaptive_mysql/scripts/run_mysql_grid_disk1_task.sh \
  A8-r1 A8 1 job memory ra-mysql-d1-job-w2 13412 imdb_job 4 3.75 1
