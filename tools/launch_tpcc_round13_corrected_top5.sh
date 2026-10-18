#!/usr/bin/env bash
set -euo pipefail

ROUND=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/gradient_debug_round13_exact_plan_regime_corrected_v1_20260729
TOP5="$ROUND/top5.json"
RUNNER="$ROUND/run_strict_serial.py"
REPLAY="$ROUND/top5_real_replay_round13_trusted3_probe2_v1"
BASELINE=/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/candidate_measurements/point_20260729_022349_1052396/summary.json

test "$(sha256sum "$TOP5" | awk '{print $1}')" = 335840e0d70f6353d45e09efbf4914eb83dd56f436df0d8e150a5716b1184d9c
test "$(sha256sum "$RUNNER" | awk '{print $1}')" = 0a20dc13455070b94ce9ce30737f80001030dc8e3f9fde16d3c9e57887f1ef76
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
sleep 5
kill -0 "$pid"
test "$(jq -r .real_workload_invocation_count "$ROUND/strict_top5_real_replay_round13_ledger_v1.json")" = 1
printf 'TPCC_ROUND13_STARTED pid=%s replay=%s\n' "$pid" "$REPLAY"
