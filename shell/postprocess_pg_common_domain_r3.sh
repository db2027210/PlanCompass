#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
r3_pid=${PG_COMMON_DOMAIN_R3_PID:-3591970}
r3_root=/disk1/resource_adaptive/pg_common_domain_frozen_champions_20260826/20260826_four_scene_common_domain_r3
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python

while kill -0 "${r3_pid}" 2>/dev/null; do
    sleep 20
done

phase=$(jq -r '.phase // ""' "${r3_root}/state.json")
if [[ "${phase}" != "complete" ]]; then
    echo "r3 terminal phase=${phase}; refusing final comparison generation" >&2
    exit 1
fi

PYTHONPATH=${project_root} "${python_bin}" -B \
    "${project_root}/shell/verify_pg_common_domain_frozen_champions.py" \
    "${r3_root}" \
    --output "${project_root}/docs/pg_common_domain_frozen_champion_verification_20260826.json"

PYTHONPATH=${project_root} "${python_bin}" -B \
    "${project_root}/shell/build_pg_external_baseline_comparison.py" \
    --common-domain-root "${r3_root}" \
    --json-output "${project_root}/docs/pg_abc_gptuner_aqetuner_final_comparison_20260826.json" \
    --markdown-output "${project_root}/docs/pg_abc_gptuner_aqetuner_final_comparison_20260826.md"

echo "r3 verification and common-domain comparison complete at $(date --iso-8601=seconds)"
