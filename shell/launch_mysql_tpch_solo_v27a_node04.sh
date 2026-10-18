#!/bin/sh
set -eu

PY=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11
ENTRY=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/support_bundles/7adb292783ca1a3cf68744519e0e60e0081f2b2e2ffbe46d2476062d564b7868/qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py
TOKEN=/tmp/mysql-shared-v25b-main-agent.token
OUT=/dev/shm/ra_mysql_tpch_v2/tpch-v2-20260731t144525z-6c3809b818ae9bbba8e70884/fourflow-collector-v27/tpch-a-solo-v27a-20260802t020253p08-6c3809b8
DB=ra_mysql_tpch_v2_tpch_v2_20260731t144525z_6c3809b818ae9bbba8e7088_db
CLIENT=ra_mysql_tpch_v2_tpch_v2_20260731t144525z_6c3809b818ae9bbba8e7088_client

test "$(id -u)" -eq 0
test -x "$PY"
test -f "$ENTRY"
test -f "$TOKEN"
test -f "$OUT/qualification_solo.command.json"
test -f "$OUT/qualification_solo.authority.json"
test ! -e "$OUT/collector.pid"
test ! -e "$OUT/A_solo_v27a.evidence.json"

nohup taskset -c 0-7,36-43 "$PY" "$ENTRY" --qualification-solo \
  --bundle /mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/bundles/87ed507ed689c103781db5f96332ae7875e4773d04d69b98a7e63e3d4097b257 \
  --container "$DB" --mysql-client-container "$CLIENT" \
  --mysql-defaults-file /mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/attempts/tpch-v2-20260731t144525z-6c3809b818ae9bbba8e70884/credentials/mysql.cnf \
  --output "$OUT/A_solo_v27a.evidence.json" \
  --main-agent-token-file "$TOKEN" \
  --main-agent-authority "$OUT/qualification_solo.authority.json" \
  --command-spec "$OUT/qualification_solo.command.json" \
  >"$OUT/collector.stdout.log" 2>"$OUT/collector.stderr.log" </dev/null &
pid=$!
echo "$pid" >"$OUT/collector.pid"
echo "H_SOLO_COLLECTOR_PID=$pid"
