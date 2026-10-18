#!/bin/sh
set -eu

PY=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11
ENTRY=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/support_bundles/4369e9c9ba01a7784a45120c60e23e64bdc6be50bb2441b8ea2b1ae4f86d01cb/qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py
TOKEN=/tmp/mysql-shared-v25b-main-agent.token
OUT=/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/qualification_evidence/fourflow-collector-v28/tpcc-b-solo-v28a-20260802t023500p08-7e2e13aa
DB=ra_mysql_tpcc_v2_db_tpcc-v16-7e2e13aa-20260801t121500p08
CLIENT=ra_mysql_tpcc_v2_client_tpcc-v16-7e2e13aa-20260801t121500p08

test "$(id -u)" -eq 0
test -x "$PY"
test -f "$ENTRY"
test -f "$TOKEN"
test -f "$OUT/qualification_solo.command.json"
test -f "$OUT/qualification_solo.command.json.sha256"
test -f "$OUT/qualification_solo.authority.json"
test -f "$OUT/qualification_solo.authority.json.sha256"
test ! -e "$OUT/collector.pid"
test ! -e "$OUT/B_solo_v28a.evidence.json"

nohup taskset -c 8-15,44-51 "$PY" "$ENTRY" --qualification-solo \
  --bundle /mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/bundles/7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77 \
  --container "$DB" --mysql-client-container "$CLIENT" \
  --mysql-defaults-file /mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/config/mysql.cnf \
  --output "$OUT/B_solo_v28a.evidence.json" \
  --main-agent-token-file "$TOKEN" \
  --main-agent-authority "$OUT/qualification_solo.authority.json" \
  --command-spec "$OUT/qualification_solo.command.json" \
  >"$OUT/collector.stdout.log" 2>"$OUT/collector.stderr.log" </dev/null &
pid=$!
echo "$pid" >"$OUT/collector.pid"
echo "C_SOLO_COLLECTOR_PID=$pid"
