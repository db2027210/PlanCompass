#!/usr/bin/env python3
"""Create reproducible random points for the eight MySQL optimizer costs.

The script deliberately only generates/records points.  Applying a point and
running a workload is done by the existing MySQL adapter/runner so every run
gets the normal snapshot/rollback guarantees.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

PARAMS = [
    "io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost",
    "key_compare_cost", "disk_temptable_create_cost", "disk_temptable_row_cost",
    "memory_temptable_create_cost", "memory_temptable_row_cost",
]
# MySQL 8 compiled defaults used by the project experience table.
DEFAULTS = {
    "io_block_read_cost": 1.0, "memory_block_read_cost": 0.25,
    "row_evaluate_cost": 0.1, "key_compare_cost": 0.05,
    "disk_temptable_create_cost": 20.0, "disk_temptable_row_cost": 0.5,
    "memory_temptable_create_cost": 1.0, "memory_temptable_row_cost": 0.1,
}
BENCHMARKS = {
    "hybench_ap": "memory", "job": "memory", "imdb": "memory",
    "tpch": "memory", "hybench_tp": "cpu", "tpcc": "cpu", "hybench_xp": "io",
}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=Path("preflight/random_points_20260721.json"))
    ap.add_argument("--seed", type=int, default=20260721)
    ap.add_argument("--points-per-benchmark", type=int, default=3)
    args = ap.parse_args()
    if args.points_per_benchmark < 1:
        raise SystemExit("points-per-benchmark must be positive")
    rng = random.Random(args.seed)
    rows = []
    for benchmark, target in BENCHMARKS.items():
        for index in range(args.points_per_benchmark):
            multipliers = {
                name: math.exp(rng.uniform(math.log(.25), math.log(4.0)))
                for name in PARAMS
            }
            values = {name: DEFAULTS[name] * multipliers[name] for name in PARAMS}
            rows.append({
                "point_id": f"{benchmark}-p{index+1}", "benchmark": benchmark,
                "target_bottleneck": target, "seed": args.seed,
                "bounds_multiplier": [0.25, 4.0], "multipliers": multipliers,
                "values": values,
            })
    payload = {"version": 1, "seed": args.seed, "parameters": PARAMS,
               "defaults": DEFAULTS, "bounds_multiplier": [0.25, 4.0],
               "points": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "points": len(rows),
                      "benchmarks": len(BENCHMARKS)}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
