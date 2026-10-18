#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p history

OPTIMIZER_CONFIG=config/optimizer_config_io_bottleneck_hybench_xp_mixed_q6_force_memory_once.json \
conda run --no-capture-output -n resource_adaptive python parameter_optimizer.py 2>&1 \
  | tee "history/run_hybench_xp_mixed_q6_force_memory_once_$(date +%Y%m%d_%H%M%S).log"
