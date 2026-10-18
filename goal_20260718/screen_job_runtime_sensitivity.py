#!/usr/bin/env python3
"""Time a bounded set of plan-sensitive JOB queries at four cost endpoints."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import time

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query-dir", type=Path, default=Path("/home/anonymous/dataset/pg_data/join-order-benchmark"))
    parser.add_argument("--queries", default="1a,1b,1c,1d,2a,2b,2c,2d,7a,7b,7c,8a,8c,8d,12a,12b")
    parser.add_argument("--output", type=Path, default=Path("screen_results/job_runtime_sensitivity.json"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=13362)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default='CHANGE_ME')
    parser.add_argument("--database", default="job")
    parser.add_argument("--query-timeout-sec", type=float, default=20.0)
    parser.add_argument("--near-default", action="store_true")
    args = parser.parse_args()
    names = [value.strip().removesuffix(".sql") + ".sql" for value in args.queries.split(",") if value.strip()]
    queries = {name: (args.query_dir / name).read_text(encoding="utf-8").strip().rstrip(";") for name in names}
    client_config = {
        "host": args.host, "port": args.port, "user": args.user, "password": args.password,
        "database": args.database, "query_timeout_sec": args.query_timeout_sec,
    }
    controller = MySQLCostAdapter(MySQLCLIClient(client_config))
    snapshot = controller.snapshot()
    configurations = (
        {
            "row_high": {"row_evaluate_cost": 0.12, "io_block_read_cost": 1.0},
            "row_low": {"row_evaluate_cost": 0.08, "io_block_read_cost": 1.0},
            "io_high": {"row_evaluate_cost": 0.1, "io_block_read_cost": 1.2},
            "io_low": {"row_evaluate_cost": 0.1, "io_block_read_cost": 0.8},
        }
        if args.near_default
        else {
            "row_high": {"row_evaluate_cost": 100.0, "io_block_read_cost": 1.0},
            "row_low": {"row_evaluate_cost": 0.1, "io_block_read_cost": 1.0},
            "io_high": {"row_evaluate_cost": 0.1, "io_block_read_cost": 100.0},
            "io_low": {"row_evaluate_cost": 0.1, "io_block_read_cost": 0.01},
        }
    )

    def run_one(name: str, sql: str) -> tuple[str, dict[str, object]]:
        started = time.monotonic()
        try:
            MySQLCLIClient(client_config).execute(sql)
            return name, {"elapsed_s": time.monotonic() - started, "error": None}
        except Exception as exc:
            return name, {"elapsed_s": time.monotonic() - started, "error": str(exc)}

    results = {}
    try:
        # A first pass at the bad endpoint removes cold-cache bias from its
        # authoritative measurement without warming under the good endpoint.
        controller.apply(configurations["row_high"])
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(lambda item: run_one(*item), queries.items()))
        for label, values in configurations.items():
            controller.apply(values)
            rows = {}
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                pending = [pool.submit(run_one, name, sql) for name, sql in queries.items()]
                for future in as_completed(pending):
                    name, row = future.result()
                    rows[name] = row
            results[label] = rows
    finally:
        controller.restore(snapshot)
    ranking = []
    for name in names:
        row_high = results["row_high"][name]
        row_low = results["row_low"][name]
        io_high = results["io_high"][name]
        io_low = results["io_low"][name]
        ranking.append({
            "query": name,
            "row_speedup_high_to_low": (
                row_high["elapsed_s"] / row_low["elapsed_s"]
                if not row_high["error"] and not row_low["error"] else None
            ),
            "io_speedup_high_to_low": (
                io_high["elapsed_s"] / io_low["elapsed_s"]
                if not io_high["error"] and not io_low["error"] else None
            ),
            "measurements": {label: results[label][name] for label in configurations},
        })
    ranking.sort(key=lambda row: max(row["row_speedup_high_to_low"] or 0, row["io_speedup_high_to_low"] or 0), reverse=True)
    payload = {"configurations": configurations, "ranking": ranking}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(ranking[:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
