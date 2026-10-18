#!/usr/bin/env python3
"""Screen native read-only HyBench TP transactions under two MySQL endpoints."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
from mysql_live_measurement import measure_hybench
from mysql_workload_adapter import MySQLEndpoint


def write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--mysqld-pid", type=int, required=True)
    parser.add_argument("--queries", default="1,2,3,4,5,6,7,8")
    parser.add_argument("--bad", required=True)
    parser.add_argument("--good", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=int, default=30)
    parser.add_argument("--clients", type=int, default=16)
    parser.add_argument("--timeout-s", type=float, default=180.0)
    parser.add_argument("--extra-properties", default="{}")
    args = parser.parse_args()

    query_ids = [int(value) for value in args.queries.split(",") if value]
    if not query_ids or min(query_ids) < 1 or max(query_ids) > 8:
        raise ValueError("--queries must contain read-only TP ids in 1..8")
    endpoints = (("bad", json.loads(args.bad)), ("good", json.loads(args.good)))
    extra_properties = json.loads(args.extra_properties)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    endpoint = MySQLEndpoint("127.0.0.1", args.port, "hybench_10x", "root", "CHANGE_ME")
    controller = MySQLCostAdapter(MySQLCLIClient({
        "host": "127.0.0.1", "port": args.port, "user": "root",
        "password": "CHANGE_ME", "database": "hybench_10x", "query_timeout_sec": 30,
    }))
    snapshot = controller.snapshot()
    rows: list[dict[str, Any]] = []
    try:
        for query_id in query_ids:
            properties = {f"tp{index}_percent": (100 if index == query_id else 0)
                          for index in range(1, 19)}
            properties.update(extra_properties)
            query_row: dict[str, Any] = {"query_id": query_id}
            for endpoint_name, params in endpoints:
                controller.apply(params)
                path = args.output_dir / f"tp{query_id}_{endpoint_name}"
                payload = measure_hybench(
                    "tp", endpoint, path, mysqld_pid=args.mysqld_pid,
                    hybench_home=Path(__file__).resolve().parent / "hybench_runtime",
                    duration_s=args.duration_s, clients=args.clients, scale_factor="10x",
                    interval_s=1, timeout_s=args.timeout_s,
                    extra_properties=properties, memory_mode="sql_dynamic",
                )
                query_row[endpoint_name] = payload
            query_row["qps_gain_ratio"] = (
                query_row["good"]["db_qps"] / query_row["bad"]["db_qps"] - 1.0
            )
            query_row["cpu_drop_ratio"] = (
                1.0 - query_row["good"]["resource"]["cpu"]
                / query_row["bad"]["resource"]["cpu"]
            )
            rows.append(query_row)
            write_json(args.output_dir / "sweep.json", {"rows": rows, "complete": False})
    finally:
        controller.restore(snapshot)
    write_json(args.output_dir / "sweep.json", {"rows": rows, "complete": True})
    print(json.dumps({"path": str(args.output_dir / "sweep.json"), "rows": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
