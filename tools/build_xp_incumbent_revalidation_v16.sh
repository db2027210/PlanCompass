#!/usr/bin/env bash
set -euo pipefail

BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v6
MANIFEST=${BUNDLE}/bundle_manifest_v16_safe_frontier.json
BUILDER=${BUNDLE}/safe_frontier_tools_v16/build_xp_incumbent_revalidation_candidate.py
CONFIG=/tmp/pg_xp_unattended_20260730_1235/authoritative_round9/config.json
SELECTION=/tmp/xp_cont003_preflight_v17_20260731T1430/diagnostic_bundle/selection.json
ACCEPTED=/tmp/xp_cont003_preflight_v17_20260731T1430/accepted_incumbent.json
QUERIES=${BUNDLE}/authority_refresh_accepted4_v4_20260731/query_snapshot_v3.json
FORMAL_ROOT=/tmp/pg_xp_unattended_20260730_1235/authoritative_round9
FORMAL_STATE=/home/anonymous/program/resource_adaptive/history/xp_autonomous_formal_target10_v1_20260731/state.json
OUTPUT=/tmp/xp_incumbent_revalidation_candidate_v16_20260731.json
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3

test "$(sha256sum "${MANIFEST}" | awk '{print $1}')" = \
    e6b6a56877e6808ba556eece2bc12b08ad9f516452d86e4b91685404042703b3
test "$(sha256sum "${BUILDER}" | awk '{print $1}')" = \
    cc4bf0f11e1f5830e9c5b7364ae8e7911e62305b0664e290c30662dade039f57
test "$(sha256sum "${CONFIG}" | awk '{print $1}')" = \
    264fea34b29e8dbe52f5b5e8652816a47a013c039ff94cf4f018395ebc3c887c
test "$(sha256sum "${ACCEPTED}" | awk '{print $1}')" = \
    9c3eab484db511e4f867924b0203de0f503a8aaa7ecf201ec0dd92356c7a0657
test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = \
    2efe6f2351d21338dc2f6cc4197e12dfc8c32b11ee109cbd84ee0cb9d0c55593
test ! -e "${OUTPUT}"

"${PYTHON}" "${BUNDLE}/xp_round10_deploy.py" verify \
    --bundle-dir "${BUNDLE}" \
    --manifest "${MANIFEST}"

exec taskset -c 32-51,288-307 \
    "${PYTHON}" -u "${BUILDER}" \
    --bundle-root "${BUNDLE}" \
    --config "${CONFIG}" \
    --selection "${SELECTION}" \
    --accepted-incumbent "${ACCEPTED}" \
    --query-snapshot "${QUERIES}" \
    --formal-root "${FORMAL_ROOT}" \
    --output "${OUTPUT}" \
    --host 127.0.0.1 \
    --port 59630 \
    --user postgres \
    --password postgres \
    --dbname hybench_sf10x
