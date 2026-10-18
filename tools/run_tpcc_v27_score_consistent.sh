#!/usr/bin/env bash
set -euo pipefail

cd /home/anonymous/program/resource_adaptive

test "$(sha256sum /tmp/tpcc_final_fresh0_autonomous_v45_score_consistent_20260802/manifest.json | awk '{print $1}')" = "caeee409bd97ad117a65b2d25b17644b8cbb9f2150cad9a978369e5ab26a3ab7"
test "$(sha256sum history/tpcc_final_algorithm_fresh10_v29_20260731T2005/formal_state.json | awk '{print $1}')" = "8575cb37b666afda71ab0bf4ffeb85cf007ca919ca2143709d014fac34b539ca"
test "$(sha256sum tools/tpcc_score_consistent_representative.py | awk '{print $1}')" = "3e8972eb81ed0e111e1ab42e15817e6bc2797645aab1f4f8f68e0fa1e22da740"
test "$(sha256sum tools/build_tpcc_exact_plan_repeat_validation_v3.py | awk '{print $1}')" = "77f87a2d2d51a18c6eebbc5acc84d48b5a47a521a9b061a235bb7a8a3ebac5f7"
test "$(sha256sum tools/tpcc_autonomous_target10_controller_v27_score_consistent.py | awk '{print $1}')" = "884b17a670177097fd536d6bb59757ee86dd295f4e6894645da6f962a1a6d271"

exec /home/anonymous/miniconda3/envs/resource_adaptive/bin/python -u \
    tools/tpcc_autonomous_target10_controller_v27_score_consistent.py \
    /tmp/tpcc_final_fresh0_autonomous_v45_score_consistent_20260802/manifest.json \
    --armed \
    --manifest-sha256 \
    caeee409bd97ad117a65b2d25b17644b8cbb9f2150cad9a978369e5ab26a3ab7
