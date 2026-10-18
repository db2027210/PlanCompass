#!/bin/sh
set -eu
PY=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11
ENTRY=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/support_bundles/e9de552036938aa3ce8514e951f1e6ef5b124da81f024e3190617c09d66ec401/qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py
TOKEN=/tmp/mysql-tpcc-solo-v21-main-agent.token
O=/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/qualification_evidence/fourflow-collector-v21/tpcc-b-solo-v21s-20260801t223000p08-7e2e13aa
test "$(id -u)" -eq 0; test -x "$PY"; test -f "$ENTRY"; test -f "$TOKEN"
nohup taskset -c 8-15,44-51 "$PY" "$ENTRY" --qualification-solo \
 --bundle /mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/bundles/7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77 \
 --container ra_mysql_tpcc_v2_db_tpcc-v16-7e2e13aa-20260801t121500p08 \
 --mysql-client-container ra_mysql_tpcc_v2_client_tpcc-v16-7e2e13aa-20260801t121500p08 \
 --mysql-defaults-file /mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/config/mysql.cnf \
 --output "$O/B_solo_v21s.evidence.json" --main-agent-token-file "$TOKEN" \
 --main-agent-authority "$O/qualification_solo.authority.json" --command-spec "$O/qualification_solo.command.json" \
 >"$O/collector.stdout.log" 2>"$O/collector.stderr.log" </dev/null &
PID=$!; echo "$PID" >"$O/collector.pid"; echo "TPCC_SOLO_COLLECTOR_PID=$PID"
