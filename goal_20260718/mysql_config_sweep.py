#!/usr/bin/env python3
"""Replay a bounded list of real MySQL configurations for endpoint diagnosis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
from mysql_live_measurement import measure_benchbase, measure_benchbase_multiprocess
from mysql_workload_adapter import MySQLEndpoint


def write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--mysqld-pid", type=int, required=True)
    parser.add_argument("--configs", required=True, help="JSON list of named parameter objects")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=int, default=20)
    parser.add_argument("--terminals", type=int, default=3)
    parser.add_argument("--client-processes", type=int, default=1)
    parser.add_argument("--scale-factor", type=float, default=10.0)
    parser.add_argument("--weights", default="100,0,0,0,0")
    parser.add_argument(
        "--rate-limit", type=float, default=0.0,
        help="positive total requests/s cap; zero keeps BenchBase unlimited",
    )
    parser.add_argument("--timeout-s", type=float, default=180.0)
    args = parser.parse_args()

    configs = json.loads(args.configs)
    if not isinstance(configs, list) or not configs:
        raise ValueError("--configs must be a non-empty JSON list")
    for row in configs:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError("each config needs a string name")
        if not isinstance(row.get("params"), dict):
            raise ValueError("each config needs a params object")

    transaction_weights = [float(value) for value in args.weights.split(",")]
    if len(transaction_weights) != 5:
        raise ValueError("--weights needs five values")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    endpoint = MySQLEndpoint("127.0.0.1", args.port, "tpcc", "root", "CHANGE_ME")
    controller = MySQLCostAdapter(MySQLCLIClient({
        "host": "127.0.0.1", "port": args.port, "user": "root",
        "password": "CHANGE_ME", "database": "tpcc", "query_timeout_sec": 30,
    }))
    snapshot = controller.snapshot()
    rows: list[dict[str, Any]] = []
    try:
        for index, config in enumerate(configs, 1):
            params = {str(key): float(value) for key, value in config["params"].items()}
            controller.apply(params)
            label = f"{index:02d}_{config['name']}"
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
            payload = measure(
                "tpcc", endpoint, args.output_dir / label,
                mysqld_pid=args.mysqld_pid,
                jar_path=Path(__file__).resolve().parent / "benchbase" / "benchbase.jar",
                duration_s=args.duration_s, **concurrency,
                scale_factor=args.scale_factor, interval_s=1,
                timeout_s=args.timeout_s, transaction_weights=transaction_weights,
                rate_limit=(args.rate_limit if args.rate_limit > 0.0 else None),
                memory_mode="rss",
            )
            rows.append({"name": config["name"], "params": params, "measurement": payload})
            write_json(args.output_dir / "sweep.json", {"rows": rows, "complete": False})
    finally:
        controller.restore(snapshot)
    write_json(args.output_dir / "sweep.json", {"rows": rows, "complete": True})
    print(json.dumps({
        "path": str(args.output_dir / "sweep.json"),
        "rows": len(rows),
        "best_qps": max(row["measurement"]["db_qps"] for row in rows),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
