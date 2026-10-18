#!/usr/bin/env bash
set -u

cd /home/anonymous/program/resource_adaptive || exit 2

export BINARY_PLAN_TUNING_HYBENCHXP_SKIP_PREPARE=1
exec /home/anonymous/miniconda3/envs/resource_adaptive/bin/python \
  binary_plan_resource_tuner.py \
  --config config/binary_plan_tuning_hybenchxp.json \
  --output-dir /disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/hyxp_none_physio_cacheevict_formal_search_20260708_170331 \
  binary-search \
  --target none
