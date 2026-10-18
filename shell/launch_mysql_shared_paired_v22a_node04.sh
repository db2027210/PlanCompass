#!/bin/sh
set -eu
PY=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11
ENTRY=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/support_bundles/2ec8a02d73fe2fca7d9b357a3ec987ab78217142c2fcfcecb8906ad4c6842714/qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py
TOKEN=/tmp/mysql-shared-v22a-main-agent.token
H=/dev/shm/ra_mysql_tpch_v2/tpch-v2-20260731t144525z-6c3809b818ae9bbba8e70884/fourflow-collector-v22/tpch-a-paired-v22a-20260801t225000p08-6c3809b8
C=/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/qualification_evidence/fourflow-collector-v22/tpcc-b-paired-v22a-20260801t225000p08-7e2e13aa
HDB=ra_mysql_tpch_v2_tpch_v2_20260731t144525z_6c3809b818ae9bbba8e7088_db
HCL=ra_mysql_tpch_v2_tpch_v2_20260731t144525z_6c3809b818ae9bbba8e7088_client
CDB=ra_mysql_tpcc_v2_db_tpcc-v16-7e2e13aa-20260801t121500p08
CCL=ra_mysql_tpcc_v2_client_tpcc-v16-7e2e13aa-20260801t121500p08
test "$(id -u)" -eq 0
test -x "$PY"
test -f "$ENTRY"
test -f "$TOKEN"
test -f "$H/qualification_solo.command.json"
test -f "$C/qualification_solo.command.json"
nohup taskset -c 8-15,44-51 "$PY" "$ENTRY" --qualification-paired \
 --bundle /mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/bundles/7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77 \
 --container "$CDB" --mysql-client-container "$CCL" \
 --mysql-defaults-file /mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/config/mysql.cnf \
 --output "$C/B_paired_v22a.evidence.json" --main-agent-token-file "$TOKEN" \
 --main-agent-authority "$C/qualification_solo.authority.json" --command-spec "$C/qualification_solo.command.json" \
 --peer-readback "$C/peer_readback.tpch.json" --peer-container "$HDB" >"$C/collector.stdout.log" 2>"$C/collector.stderr.log" </dev/null &
CPID=$!
echo "$CPID" >"$C/collector.pid"
sleep 1
nohup taskset -c 0-7,36-43 "$PY" "$ENTRY" --qualification-paired \
 --bundle /mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/bundles/87ed507ed689c103781db5f96332ae7875e4773d04d69b98a7e63e3d4097b257 \
 --container "$HDB" --mysql-client-container "$HCL" \
 --mysql-defaults-file /mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/attempts/tpch-v2-20260731t144525z-6c3809b818ae9bbba8e70884/credentials/mysql.cnf \
 --output "$H/A_paired_v22a.evidence.json" --main-agent-token-file "$TOKEN" \
 --main-agent-authority "$H/qualification_solo.authority.json" --command-spec "$H/qualification_solo.command.json" \
 --peer-readback "$H/peer_readback.tpcc.json" --peer-container "$CDB" >"$H/collector.stdout.log" 2>"$H/collector.stderr.log" </dev/null &
HPID=$!
echo "$HPID" >"$H/collector.pid"
echo "C_COLLECTOR_PID=$CPID H_COLLECTOR_PID=$HPID"
