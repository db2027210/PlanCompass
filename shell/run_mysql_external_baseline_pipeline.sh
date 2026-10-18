#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
binding=${project_root}/config/formal/mysql_external_baseline_independent_workers_20260826_v1.json
campaign_root=/disk2/resource_adaptive_mysql_external_baselines_20260826/20260826_gptuner_aqetuner_four_r7
runner=${project_root}/shell/run_mysql_four_scene_external_baselines.py
prepare=${project_root}/shell/prepare_mysql_external_baseline_clones.py

test -f "${binding}"
mkdir -p "${campaign_root}"

export PYTHONPATH="/home/anonymous/program/resource_adaptive_mysql:${project_root}"
export PYTHONUNBUFFERED=1

"${python_bin}" -B "${prepare}" \
    --binding-output "${binding}" \
    --start-existing-and-refresh-runtime-authority

"${python_bin}" -B "${prepare}" \
    --binding-output "${binding}" \
    --archive-orphaned-gptuner-baseline-quarantine

if test ! -f "${campaign_root}/gptuner/authority_manifest.json"; then
    "${python_bin}" -B -u "${runner}" \
        --method gptuner \
        --worker-binding "${binding}" \
        --output-dir "${campaign_root}/gptuner" \
        --candidate-budget 18 \
        --seed 20260826
fi

if test ! -f "${campaign_root}/aqetuner/authority_manifest.json"; then
    "${python_bin}" -B -u "${runner}" \
        --method aqetuner \
        --worker-binding "${binding}" \
        --output-dir "${campaign_root}/aqetuner" \
        --candidate-budget 18 \
        --seed 20260830
fi
