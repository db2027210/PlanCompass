#!/usr/bin/env bash
set -euo pipefail

project_root=${PROJECT_ROOT:-/home/anonymous/program/resource_adaptive}
external_root=${PG_XPIO_EXTERNAL_ROOT:-/disk1/resource_adaptive/pg_external_xpio_correct_domain_20260826/20260826_xpio_40cpu24g_r1}
r4_root=${PG_COMMON_DOMAIN_R4_ROOT:-/disk1/resource_adaptive/pg_common_domain_frozen_champions_20260826/20260826_four_scene_common_domain_r4_corrected_xpio}
handoff_authority=${external_root}/r4_handoff_authority.json
retry_runner=${project_root}/shell/run_pg_common_domain_r4_with_retries.sh
postprocessor=${project_root}/shell/postprocess_pg_common_domain_r4.sh
python_bin=${PYTHON_BIN:-/home/anonymous/miniconda3/envs/py310/bin/python}

while [[ ! -f ${handoff_authority} ]]; do
    sleep 10
done

r4_pid=$(jq -er '.final_common_domain_pid | numbers' "${handoff_authority}")
while kill -0 "${r4_pid}" 2>/dev/null; do
    sleep 10
done

if [[ -f ${r4_root}/authority_manifest.json ]] \
    && jq -e '.contract == "pg_four_scene_adjacent_bracketed_frozen_champion_replay_v2" and .phase == "complete"' \
        "${r4_root}/authority_manifest.json" >/dev/null; then
    echo "original r4 campaign completed; recovery not needed"
    exit 0
fi

echo "original r4 campaign failed closed; preserving evidence and starting clean retry" >&2
env \
    PROJECT_ROOT="${project_root}" \
    PYTHON_BIN="${python_bin}" \
    PG_COMMON_DOMAIN_R4_ROOT="${r4_root}" \
    PG_GPTUNER_XPIO_AUTHORITY="${external_root}/gptuner/xp_io/authority_manifest.json" \
    PG_AQETUNER_XPIO_AUTHORITY="${external_root}/aqetuner/xp_io/authority_manifest.json" \
    PG_COMMON_DOMAIN_MAX_CAMPAIGN_ATTEMPTS=3 \
    taskset -c 104-131,144-159 bash "${retry_runner}"

env \
    PG_COMMON_DOMAIN_R4_PID=2147483647 \
    PG_COMMON_DOMAIN_R4_ROOT="${r4_root}" \
    PG_XPIO_EXTERNAL_ROOT="${external_root}" \
    bash "${postprocessor}"
