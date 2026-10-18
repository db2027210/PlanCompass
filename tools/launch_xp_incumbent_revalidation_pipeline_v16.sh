#!/usr/bin/env bash
set -euo pipefail

PROJECT=/home/anonymous/program/resource_adaptive
BUNDLE=/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v6
BUILD=${PROJECT}/tools/build_xp_incumbent_revalidation_v16.sh
RUNNER=${BUNDLE}/safe_frontier_tools_v16/run_xp_incumbent_revalidation_once.py
CANDIDATE=/tmp/xp_incumbent_revalidation_candidate_v16_20260731.json
ACCEPTED=/tmp/xp_cont003_preflight_v17_20260731T1430/accepted_incumbent.json
FORMAL_STATE=${PROJECT}/history/xp_autonomous_formal_target10_v1_20260731/state.json
CONFIG=/tmp/pg_xp_unattended_20260730_1235/authoritative_round9/config.json
LEGACY=${BUNDLE}/xp_legacy_replay_driver.py
OUTPUT=/tmp/xp_incumbent_revalidation_once_v16_20260731
PYTHON=/home/anonymous/miniconda3/envs/py310/bin/python3

test "$(sha256sum "${BUILD}" | awk '{print $1}')" = \
    9e491417f83d8561c98c201876576b13d87d1f3ade02636bc36958729f0bfff8
test "$(sha256sum "${RUNNER}" | awk '{print $1}')" = \
    6085241146f8bd1fc24f96b9be562fe9315f68427ed5403c5af7e050ab3dc1b7
test "$(sha256sum "${ACCEPTED}" | awk '{print $1}')" = \
    9c3eab484db511e4f867924b0203de0f503a8aaa7ecf201ec0dd92356c7a0657
test "$(sha256sum "${FORMAL_STATE}" | awk '{print $1}')" = \
    2efe6f2351d21338dc2f6cc4197e12dfc8c32b11ee109cbd84ee0cb9d0c55593
test "$(sha256sum "${CONFIG}" | awk '{print $1}')" = \
    264fea34b29e8dbe52f5b5e8652816a47a013c039ff94cf4f018395ebc3c887c
test "$(sha256sum "${LEGACY}" | awk '{print $1}')" = \
    22e03298e33105573fcba68bcd5fe08cbcdd06e939c7382f8ebbc65202dd076e
test ! -e "${CANDIDATE}"
test ! -e "${OUTPUT}"

bash "${BUILD}"
CANDIDATE_SHA="$(sha256sum "${CANDIDATE}" | awk '{print $1}')"

COMMON_ARGS=(
    --candidate "${CANDIDATE}"
    --candidate-sha256 "${CANDIDATE_SHA}"
    --accepted-incumbent "${ACCEPTED}"
    --accepted-incumbent-sha256
    9c3eab484db511e4f867924b0203de0f503a8aaa7ecf201ec0dd92356c7a0657
    --formal-state "${FORMAL_STATE}"
    --formal-state-sha256
    2efe6f2351d21338dc2f6cc4197e12dfc8c32b11ee109cbd84ee0cb9d0c55593
    --config "${CONFIG}"
    --config-sha256
    264fea34b29e8dbe52f5b5e8652816a47a013c039ff94cf4f018395ebc3c887c
    --legacy-driver "${LEGACY}"
    --legacy-driver-sha256
    22e03298e33105573fcba68bcd5fe08cbcdd06e939c7382f8ebbc65202dd076e
    --output-dir "${OUTPUT}"
)

taskset -c 32-51,288-307 \
    "${PYTHON}" -u "${RUNNER}" "${COMMON_ARGS[@]}" --preflight-only
exec taskset -c 32-51,288-307 \
    "${PYTHON}" -u "${RUNNER}" "${COMMON_ARGS[@]}"
