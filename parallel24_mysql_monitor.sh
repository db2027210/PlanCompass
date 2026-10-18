#!/usr/bin/env bash
set -u
root="/home/anonymous/program/resource_adaptive_mysql/history/parallel24_20260721_v1/mysql/brute_force"
mkdir -p "$root/monitor"
while true; do
  {
    date --iso-8601=seconds
    docker ps --filter 'name=p24_mysql_brute_force_' --format 'container={{.Names}} status={{.Status}} ports={{.Ports}}'
    ps -eo pid,etimes,pcpu,pmem,args | rg 'parallel24_mysql_bruteforce.py|HyBenchFormalMain|FormalSQLWorkload|benchbase.jar' | rg -v 'rg ' || true
    for item in ap:57101 job:57102 tpch:57103 xp:57104; do
      name="${item%%:*}"
      port="${item##*:}"
      active=$(mysql --defaults-extra-file=/home/anonymous/.my.cnf -h127.0.0.1 -P"$port" -NBe "SELECT COUNT(*) FROM information_schema.processlist WHERE COMMAND <> 'Sleep';" 2>/dev/null || printf unavailable)
      printf 'db=%s port=%s active_connections=%s\n' "$name" "$port" "$active"
    done
  } | tee -a "$root/monitor/status_60s.log"
  sleep 60
done
