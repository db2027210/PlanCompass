#!/usr/bin/env bash
set +e
cd /home/anonymous/program/resource_adaptive_mysql || exit 1
while true; do
  date '+P24 mysql/random_walk status %F %T %z'
  timeout 20 docker ps -a --format '{{.Names}} {{.Status}} {{.Ports}}' | rg '^p24_mysql_random_walk_' || true
  for port in 57201 57202 57203 57204; do
    printf 'port=%s activity=' "$port"
    timeout 5 mysql --defaults-extra-file=/home/anonymous/.my.cnf -h127.0.0.1 -P"$port" -NBe \
      "SELECT CONCAT(COUNT(*),':',COALESCE(GROUP_CONCAT(COMMAND),'')) FROM information_schema.processlist WHERE ID<>CONNECTION_ID()" \
      2>/dev/null || printf 'unavailable'
    printf '\n'
  done
  ps -eo pid,etimes,args | rg 'parallel24_mysql_random_walk.py|parallel24_prepare_random_tpch.py|HyBenchFormalMain|FormalSQLWorkload|benchbase' | rg -v 'rg ' || true
  sleep 60
done
