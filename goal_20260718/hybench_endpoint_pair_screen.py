#!/usr/bin/env python3
"""Real native HyBench bad/good endpoint screen with exact cost restoration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
from mysql_live_measurement import measure_hybench
from mysql_workload_adapter import MySQLEndpoint


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("ap", "tp", "xp"))
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--mysqld-pid", type=int, required=True)
    parser.add_argument("--clients", type=int, default=2)
    parser.add_argument("--bad", required=True)
    parser.add_argument("--good", required=True)
    parser.add_argument("--extra-properties", default="{}")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--hybench-home", type=Path,
        default=Path(__file__).resolve().parent / "hybench_runtime",
    )
    args = parser.parse_args()
    bad, good = json.loads(args.bad), json.loads(args.good)
    extra = json.loads(args.extra_properties)
    endpoint = MySQLEndpoint("127.0.0.1", args.port, "hybench_10x", "root", "CHANGE_ME")
    controller = MySQLCostAdapter(MySQLCLIClient({
        "host": "127.0.0.1", "port": args.port, "user": "root",
        "password": "CHANGE_ME", "database": "hybench_10x", "query_timeout_sec": 30,
    }))
    snapshot = controller.snapshot()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    def measure(label: str) -> dict:
        return measure_hybench(
            args.mode, endpoint, args.output_dir / label,
            mysqld_pid=args.mysqld_pid,
            hybench_home=args.hybench_home,
            duration_s=60, clients=args.clients, scale_factor="10x",
            interval_s=1, timeout_s=360, extra_properties=extra,
            memory_mode="sql_dynamic",
        )
    try:
        controller.apply(bad)
        measure("01_bad_warmup")
        baseline = measure("02_bad_baseline")
        controller.apply(good)
        good_first = measure("03_good_first")
        good_final = measure("04_good_independent")
    finally:
        controller.restore(snapshot)
    first_gain = good_first["db_qps"] / baseline["db_qps"] - 1
    final_gain = good_final["db_qps"] / baseline["db_qps"] - 1
    payload = {
        "mode": args.mode, "bad": bad, "good": good, "extra_properties": extra,
        "baseline": baseline, "good_first": good_first, "good_independent": good_final,
        "qps_gain_first_ratio": first_gain,
        "qps_gain_independent_ratio": final_gain,
        "qps_15pct_reached_twice": first_gain >= .15 and final_gain >= .15,
    }
    path = args.output_dir / "endpoint_screen.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(path), "gain_first": first_gain,
                      "gain_independent": final_gain,
                      "reached_twice": payload["qps_15pct_reached_twice"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
