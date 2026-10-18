#!/usr/bin/env bash
set -euo pipefail

PYTHON=/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11
WRAPPER=/tmp/tpch_fresh0_target10_v36_compensated.py
BASE=/tmp/tpch_fresh0_target10_loop_v36_base.py
EXPANDER=/tmp/tpch_exact_regime_neighbor_expand_v4_compensated_plan_preserving.py
BUILDER=/tmp/build_tpch_autonomous_successor_top5_v7_incumbent_plan_probe.py

test "$(sha256sum "${WRAPPER}" | awk '{print $1}')" = "88e585855cdf42c5866144a104bb19c48bdacc0b089f114213eef536e6712cef"
test "$(sha256sum "${BASE}" | awk '{print $1}')" = "34107e91b4bcfd0b2fe8d2911a0855e1512fc881665f4db3c3cb67618bd055fb"
test "$(sha256sum "${EXPANDER}" | awk '{print $1}')" = "5d8e6da67ed4207b6a5b80d268bf695d4c2a4de4d925d685bb607a7d7fd155f5"
test "$(sha256sum "${BUILDER}" | awk '{print $1}')" = "27c8dfdcf16f2387c469d5d133c8dc9c38154419c1ac3575a5c57260f3273b8d"

"${PYTHON}" -u "${WRAPPER}" --preflight-only
exec "${PYTHON}" -u "${WRAPPER}"
