#!/usr/bin/env bash
set -euo pipefail

readonly LOCK_FILE=/tmp/resource_adaptive_mysql_ap_csv_sync_20260723.lock
readonly SOURCE_DIR=/home/anonymous/HyBench-2024-master/Data_10x
readonly TARGET=anonymous@192.0.2.3:/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap/data/
readonly LOG_DIR=/home/anonymous/program/resource_adaptive_mysql/history/mysql_sgd_env_agents_20260723/ap

mkdir -p "$LOG_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || {
  echo "AP CSV sync already owns $LOCK_FILE" >&2
  exit 75
}

rsync -a --partial --info=progress2 \
  --log-file="$LOG_DIR/rsync_20260723.log" \
  "$SOURCE_DIR"/*.csv "$TARGET"
