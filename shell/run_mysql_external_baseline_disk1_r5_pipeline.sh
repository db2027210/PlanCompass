#!/usr/bin/env bash
set -euo pipefail

project_root=/home/anonymous/program/resource_adaptive
mysql_root=/home/anonymous/program/resource_adaptive_mysql
python_bin=/home/anonymous/miniconda3/envs/py310/bin/python
binding=${project_root}/config/formal/mysql_external_baseline_independent_workers_disk1_20260826_v4.json
campaign_root=/disk1/resource_adaptive/mysql_external_baselines_20260826/20260826_gptuner_aqetuner_four_disk1_r5_affinity_replacement_clean
gptuner_root=${campaign_root}/gptuner_disk1_r5
aqetuner_root=${campaign_root}/aqetuner_disk1_r5
runner=${project_root}/shell/run_mysql_four_scene_external_baselines.py

cd "${project_root}"
test -f "${binding}"
test -f "${binding}.sha256"
test "$(sha256sum "${binding}" | awk '{print $1}')" = "$(awk '{print $1}' "${binding}.sha256")"
test ! -e "${campaign_root}"

export PYTHONPATH="${mysql_root}:${project_root}/shell:${project_root}"
export PYTHONUNBUFFERED=1

"${python_bin}" -B -u "${runner}" \
    --method gptuner \
    --worker-binding "${binding}" \
    --output-dir "${gptuner_root}" \
    --candidate-budget 18 \
    --seed 20260826

"${python_bin}" -B -u "${runner}" \
    --method aqetuner \
    --worker-binding "${binding}" \
    --output-dir "${aqetuner_root}" \
    --candidate-budget 18 \
    --seed 20260830

export MYSQL_EXTERNAL_CAMPAIGN_ROOT=${campaign_root}
export MYSQL_EXTERNAL_GPTUNER_ROOT=${gptuner_root}
export MYSQL_EXTERNAL_AQETUNER_ROOT=${aqetuner_root}
exec /bin/bash "${project_root}/shell/postprocess_mysql_external_baselines_r8.sh"
