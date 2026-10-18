#!/usr/bin/env bash
set -u

unit="$1"
device="$2"
container="$3"
limit_pct="${4:-85}"
interval_s="${5:-5}"
hits=0

read_busy_ms() {
  awk '{print $10}' "/sys/block/${device}/stat"
}

previous="$(read_busy_ms)"
while systemctl --user is-active --quiet "$unit"; do
  sleep "$interval_s"
  current="$(read_busy_ms)"
  delta=$((current - previous))
  previous="$current"
  util=$((delta * 100 / (interval_s * 1000)))
  if (( util >= limit_pct )); then
    hits=$((hits + 1))
  else
    hits=0
  fi
  if (( hits >= 2 )); then
    systemctl --user stop "$unit"
    docker exec "$container" mysql -uroot -pCHANGE_ME -e "SET GLOBAL innodb_buffer_pool_size=2147483648; SET GLOBAL innodb_adaptive_hash_index=ON; SET GLOBAL innodb_spin_wait_delay=6; SET GLOBAL innodb_spin_wait_pause_multiplier=50; SET GLOBAL innodb_sync_spin_loops=30; SET GLOBAL innodb_flush_log_at_trx_commit=2; UPDATE mysql.server_cost SET cost_value=NULL; UPDATE mysql.engine_cost SET cost_value=NULL; FLUSH OPTIMIZER_COSTS;" >/dev/null 2>&1 || true
    exit 85
  fi
done
