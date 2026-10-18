#!/bin/sh
set -eu
PY=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11
ENTRY=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/support_bundles/e9de552036938aa3ce8514e951f1e6ef5b124da81f024e3190617c09d66ec401/qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py
TOKEN=/tmp/mysql-tpch-solo-v21-main-agent.token
O=/dev/shm/ra_mysql_tpch_v2/tpch-v2-20260731t144525z-6c3809b818ae9bbba8e70884/fourflow-collector-v21/tpch-a-solo-v21s-20260801t223000p08-6c3809b8
test "$(id -u)" -eq 0; test -x "$PY"; test -f "$ENTRY"; test -f "$TOKEN"
nohup taskset -c 0-7,36-43 "$PY" "$ENTRY" --qualification-solo \
 --bundle /mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/bundles/87ed507ed689c103781db5f96332ae7875e4773d04d69b98a7e63e3d4097b257 \
 --container ra_mysql_tpch_v2_tpch_v2_20260731t144525z_6c3809b818ae9bbba8e7088_db \
 --mysql-client-container ra_mysql_tpch_v2_tpch_v2_20260731t144525z_6c3809b818ae9bbba8e7088_client \
 --mysql-defaults-file /mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/attempts/tpch-v2-20260731t144525z-6c3809b818ae9bbba8e70884/credentials/mysql.cnf \
 --output "$O/A_solo_v21s.evidence.json" --main-agent-token-file "$TOKEN" \
 --main-agent-authority "$O/qualification_solo.authority.json" --command-spec "$O/qualification_solo.command.json" \
 >"$O/collector.stdout.log" 2>"$O/collector.stderr.log" </dev/null &
PID=$!; echo "$PID" >"$O/collector.pid"; echo "TPCH_SOLO_COLLECTOR_PID=$PID"
