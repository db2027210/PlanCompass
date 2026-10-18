#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export OPTIMIZER_CONFIG="${OPTIMIZER_CONFIG:-config/optimizer_config_memory_finetune.json}"
export METRICS_SOURCE="${METRICS_SOURCE:-vm-runtime}"

python parameter_optimizer.py
