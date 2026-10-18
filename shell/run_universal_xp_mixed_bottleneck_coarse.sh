#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p history config/generated

python shell/run_universal_xp_mixed_bottleneck_coarse.py "$@"
