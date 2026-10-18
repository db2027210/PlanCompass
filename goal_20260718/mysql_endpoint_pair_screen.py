#!/usr/bin/env python3
"""Bounded real bad/good endpoint screen with exact cost restoration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
from mysql_live_measurement import measure_benchbase, measure_benchbase_multiprocess, measure_job
from mysql_workload_adapter import MySQLEndpoint


def weights(spec: str, count: int) -> list[float] | None:
    if not spec:
        return None
    selected = [float(value) for value in spec.split(",")]
    if len(selected) != count:
        raise ValueError(f"expected {count} weights")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", choices=("tpch", "tpcc"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--mysqld-pid", type=int, required=True)
    parser.add_argument("--bad", required=True)
    parser.add_argument("--good", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=int, default=30)
    parser.add_argument("--terminals", type=int, default=1)
    parser.add_argument("--client-processes", type=int, default=1)
    parser.add_argument(
        "--rate-limit", type=float, default=0.0,
        help="positive total requests/s cap; zero keeps BenchBase unlimited",
    )
    parser.add_argument("--scale-factor", type=float, required=True)
    parser.add_argument("--weights", default="")
    parser.add_argument("--native-manifest", type=Path)
    parser.add_argument("--native-repetitions", type=int, default=1)
    parser.add_argument(
        "--memory-mode",
        choices=("rss", "sql_dynamic", "sql_total"),
        default="sql_dynamic",
    )
    parser.add_argument("--timeout-s", type=float, default=420)
    args = parser.parse_args()
    bad, good = json.loads(args.bad), json.loads(args.good)
    endpoint = MySQLEndpoint("127.0.0.1", args.port, args.database, "root", "CHANGE_ME")
    client = MySQLCLIClient({
        "host": "127.0.0.1", "port": args.port, "user": "root",
        "password": "CHANGE_ME", "database": args.database, "query_timeout_sec": 30,
    })
    controller = MySQLCostAdapter(client)
    snapshot = controller.snapshot()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    transaction_weights = weights(args.weights, 22 if args.benchmark == "tpch" else 5)
    if args.native_manifest is not None and args.benchmark != "tpch":
        raise ValueError("--native-manifest is only valid for TPC-H")
    if args.native_repetitions < 1:
        raise ValueError("--native-repetitions must be positive")

    def measure(label: str) -> dict:
        if args.native_manifest is not None:
            payload = measure_job(
                endpoint,
                args.output_dir / label,
                mysqld_pid=args.mysqld_pid,
                manifest_path=args.native_manifest,
                concurrency=args.terminals,
                repetitions=args.native_repetitions,
                interval_s=1,
                timeout_s=args.timeout_s,
                memory_mode=args.memory_mode,
            )
            payload["throughput_source"] = "native_mysql_official_tpch_sql"
            return payload
        measure = (
            measure_benchbase_multiprocess
            if args.client_processes > 1 else measure_benchbase
        )
        concurrency = (
            {
                "processes": args.client_processes,
                "terminals_per_process": args.terminals,
            }
            if args.client_processes > 1 else {"terminals": args.terminals}
        )
        return measure(
            args.benchmark, endpoint, args.output_dir / label,
            mysqld_pid=args.mysqld_pid,
            jar_path=Path(__file__).resolve().parent / "benchbase" / "benchbase.jar",
            duration_s=args.duration_s, **concurrency,
            scale_factor=args.scale_factor, interval_s=1,
            timeout_s=args.timeout_s, transaction_weights=transaction_weights,
            rate_limit=(args.rate_limit if args.rate_limit > 0.0 else None),
            memory_mode=args.memory_mode,
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
    gain_first = good_first["db_qps"] / baseline["db_qps"] - 1
    gain_final = good_final["db_qps"] / baseline["db_qps"] - 1
    payload = {
        "benchmark": args.benchmark, "bad": bad, "good": good,
        "rate_limit": args.rate_limit if args.rate_limit > 0.0 else None,
        "baseline": baseline, "good_first": good_first, "good_independent": good_final,
        "qps_gain_first_ratio": gain_first, "qps_gain_independent_ratio": gain_final,
        "qps_15pct_reached_twice": gain_first >= .15 and gain_final >= .15,
    }
    path = args.output_dir / "endpoint_screen.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(path), "gain_first": gain_first,
                      "gain_independent": gain_final,
                      "reached_twice": payload["qps_15pct_reached_twice"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
