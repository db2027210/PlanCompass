#!/usr/bin/env python3
"""Compare one read-only SQL plan at four MySQL cost endpoints."""

from __future__ import annotations

import argparse
import json
from typing import Any

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter


def table_paths(value: Any) -> list[dict[str, Any]]:
    rows = []
    if isinstance(value, list):
        for item in value:
            rows.extend(table_paths(item))
    elif isinstance(value, dict):
        if "table_name" in value:
            rows.append({key: value.get(key) for key in
                         ("table_name", "access_type", "key", "rows_examined_per_scan")})
        for item in value.values():
            rows.extend(table_paths(item))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--sql", required=True)
    args = parser.parse_args()
    config = {"host": "127.0.0.1", "port": args.port, "user": "root",
              "password": "CHANGE_ME", "database": args.database, "query_timeout_sec": 30}
    client = MySQLCLIClient(config)
    controller = MySQLCostAdapter(client)
    snapshot = controller.snapshot()
    endpoints = {
        "row_high": {"row_evaluate_cost": 100, "io_block_read_cost": 1},
        "row_low": {"row_evaluate_cost": .001, "io_block_read_cost": 1},
        "io_high": {"row_evaluate_cost": .1, "io_block_read_cost": 100},
        "io_low": {"row_evaluate_cost": .1, "io_block_read_cost": .001},
    }
    result = {}
    try:
        for name, values in endpoints.items():
            controller.apply(values)
            plan = json.loads(client.execute("EXPLAIN FORMAT=JSON " + args.sql.rstrip(";")))
            result[name] = {"costs": values, "table_paths": table_paths(plan)}
    finally:
        controller.restore(snapshot)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
