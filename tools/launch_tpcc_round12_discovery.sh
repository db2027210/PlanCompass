#!/usr/bin/env bash
set -euo pipefail

ROUND=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/gradient_debug_round12_current_plan_discovery_v1_20260729
TOP5="$ROUND/top5.json"
RUNNER="$ROUND/run_strict_serial.py"
REPLAY="$ROUND/top5_real_replay_round12_probe5_v1"
BASELINE=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/candidate_measurements/point_20260729_022349_1052396/summary.json

test "$(sha256sum "$TOP5" | awk '{print $1}')" = 4de31b3e0c4910725c8ebf10a5f07972b0a907af11a6901ffcf30ada79c9c093
test "$(sha256sum "$RUNNER" | awk '{print $1}')" = d67eb88dbc95d96536bba9d64fddfdef5f7ee1ab07b0cb61d9cd676cd9d328fa
test -f "$BASELINE"
test ! -e "$REPLAY/driver.pid"
mkdir -p "$REPLAY"

export PYTHONPATH="/home/anonymous/program/resource_adaptive:$ROUND"
export ADAPTIVE_REAL_REPLAY_ENABLED=0
nohup setsid /home/anonymous/miniconda3/envs/py313/bin/python -u \
    "$RUNNER" "$TOP5" "$REPLAY" "$BASELINE" \
    >"$REPLAY/driver.log" 2>&1 </dev/null &
pid=$!
printf '%s\n' "$pid" >"$REPLAY/driver.pid"
printf 'TPCC_ROUND12_STARTED pid=%s replay=%s\n' "$pid" "$REPLAY"
