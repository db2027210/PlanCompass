#!/bin/sh
set -eu

PY=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11
ENTRY=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/support_bundles/4369e9c9ba01a7784a45120c60e23e64bdc6be50bb2441b8ea2b1ae4f86d01cb/qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py
HS=/dev/shm/ra_mysql_tpch_v2/tpch-v2-20260731t144525z-6c3809b818ae9bbba8e70884/fourflow-collector-v28/tpch-a-solo-v28a-20260802t024300p08-6c3809b8/A_solo_v28a.evidence.json
HP=/dev/shm/ra_mysql_tpch_v2/tpch-v2-20260731t144525z-6c3809b818ae9bbba8e70884/fourflow-collector-v28/tpch-a-paired-v28b-20260802t025000p08-6c3809b8/A_paired_v28b.evidence.json
CS=/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/qualification_evidence/fourflow-collector-v28/tpcc-b-solo-v28a-20260802t023500p08-7e2e13aa/B_solo_v28a.evidence.json
CP=/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/qualification_evidence/fourflow-collector-v28/tpcc-b-paired-v28b-20260802t025000p08-7e2e13aa/B_paired_v28b.evidence.json
HG=/dev/shm/ra_mysql_tpch_v2/tpch-v2-20260731t144525z-6c3809b818ae9bbba8e70884/fourflow-collector-v28/tpch-a-paired-v28b-20260802t025000p08-6c3809b8/dynamic_gate.tpch.v28.json
CG=/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/qualification_evidence/fourflow-collector-v28/tpcc-b-paired-v28b-20260802t025000p08-7e2e13aa/dynamic_gate.tpcc.v28.json

test "$(id -u)" -eq 0
test -x "$PY"
test -f "$ENTRY"
for p in "$HS" "$HP" "$CS" "$CP"; do
  test -f "$p"
  test -f "$p.sha256"
done
test ! -e "$HG"
test ! -e "$CG"

"$PY" "$ENTRY" --compare-bidirectional \
  --a-solo-evidence "$HS" --a-paired-evidence "$HP" \
  --b-solo-evidence "$CS" --b-paired-evidence "$CP" \
  --gate-scenario tpch_memory --host-id node04 \
  --attempt-token tpch-v28-gate-20260802t025600p08 \
  --output "$HG"

"$PY" "$ENTRY" --compare-bidirectional \
  --a-solo-evidence "$HS" --a-paired-evidence "$HP" \
  --b-solo-evidence "$CS" --b-paired-evidence "$CP" \
  --gate-scenario tpcc_cpu --host-id node04 \
  --attempt-token tpcc-v28-gate-20260802t025600p08 \
  --output "$CG"
