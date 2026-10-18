#!/bin/sh
set -eu

PY=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11
GATE=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/support_bundles/4369e9c9ba01a7784a45120c60e23e64bdc6be50bb2441b8ea2b1ae4f86d01cb/qualification/mysql_tpch_tpcc_shared_dynamic_gate_v1.py
HS=/dev/shm/ra_mysql_tpch_v2/tpch-v2-20260731t144525z-6c3809b818ae9bbba8e70884/fourflow-collector-v28/tpch-a-solo-v28a-20260802t024300p08-6c3809b8/A_solo_v28a.evidence.json
HP=/dev/shm/ra_mysql_tpch_v2/tpch-v2-20260731t144525z-6c3809b818ae9bbba8e70884/fourflow-collector-v28/tpch-a-paired-v28c-20260802t030700p08-6c3809b8/A_paired_v28c.evidence.json
CS=/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/qualification_evidence/fourflow-collector-v28/tpcc-b-solo-v28a-20260802t023500p08-7e2e13aa/B_solo_v28a.evidence.json
CP=/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/qualification_evidence/fourflow-collector-v28/tpcc-b-paired-v28c-20260802t030700p08-7e2e13aa/B_paired_v28c.evidence.json
HG=/dev/shm/ra_mysql_tpch_v2/tpch-v2-20260731t144525z-6c3809b818ae9bbba8e70884/fourflow-collector-v28/tpch-a-paired-v28c-20260802t030700p08-6c3809b8/dynamic_gate.tpch.v28c.json
CG=/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/qualification_evidence/fourflow-collector-v28/tpcc-b-paired-v28c-20260802t030700p08-7e2e13aa/dynamic_gate.tpcc.v28c.json

test "$(id -u)" -eq 0
for f in "$PY" "$GATE" "$HS" "$HP" "$CS" "$CP"; do test -e "$f"; done
test ! -e "$HG"
test ! -e "$CG"

"$PY" "$GATE" --solo "$HS" --paired "$HP" --output "$HG" \
  --gate-scenario tpch_memory --host-id node04 \
  --rolling-window-samples 30 --maximum-cpu-delta 0.20 \
  --maximum-memory-delta 0.10 --maximum-io-delta 0.10

"$PY" "$GATE" --solo "$CS" --paired "$CP" --output "$CG" \
  --gate-scenario tpcc_cpu --host-id node04 \
  --rolling-window-samples 30 --maximum-cpu-delta 0.20 \
  --maximum-memory-delta 0.10 --maximum-io-delta 0.10

sha256sum "$HG" "$CG"
