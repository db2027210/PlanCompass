#!/usr/bin/env bash
set -euo pipefail

target_root=/disk1/resource_adaptive_mysql_grid_20260718

install -d -o anonymous -g anonymous \
  "${target_root}/workers/hybench-w3" \
  "${target_root}/workers/hybench-w5" \
  "${target_root}/workers/job-w2" \
  "${target_root}/workers/tpch-w1" \
  "${target_root}/isolated/tpcc-cpu" \
  "${target_root}/results/mysql_grid_20260718"

sync_tree() {
  local source=$1
  local target=$2
  rsync -aHAX --numeric-ids --info=progress2 "${source}/" "${target}/"
}

(
  sync_tree /disk3/ra_mysql_workers/hybench-w3 "${target_root}/workers/hybench-w3"
  sync_tree /disk3/ra_mysql_workers/job-w2 "${target_root}/workers/job-w2"
  sync_tree /disk3/ra_mysql_isolated/tpcc-cpu "${target_root}/isolated/tpcc-cpu"
) > /tmp/ra_mysql_disk1_copy_group1.log 2>&1 &
group1_pid=$!

(
  sync_tree /disk3/ra_mysql_workers/hybench-w5 "${target_root}/workers/hybench-w5"
  sync_tree /disk3/ra_mysql_workers/tpch-w1 "${target_root}/workers/tpch-w1"
) > /tmp/ra_mysql_disk1_copy_group2.log 2>&1 &
group2_pid=$!

sync_tree /disk3/ra_mysql_results/mysql_grid_20260718 \
  "${target_root}/results/mysql_grid_20260718" \
  > /tmp/ra_mysql_disk1_copy_results.log 2>&1

wait "${group1_pid}"
wait "${group2_pid}"
touch "${target_root}/migration_complete"
