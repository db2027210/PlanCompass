#!/usr/bin/env python3
"""Find cost-sensitive read statements in frozen HyBench TOML without executing writes."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import tomllib
from typing import Any

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter


def canonical(value: Any) -> Any:
    if isinstance(value, list):
        return [canonical(item) for item in value]
    if isinstance(value, dict):
        ignored = {"cost_info", "rows_examined_per_scan", "rows_produced_per_join",
                   "filtered", "data_read_per_join"}
        return {key: canonical(item) for key, item in value.items() if key not in ignored}
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--prefix", default="TP-")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    statement_file = Path(__file__).resolve().parent / "hybench_runtime" / "conf" / "stmt_mysql.toml"
    document = tomllib.loads(statement_file.read_text(encoding="utf-8"))
    statements = {}
    for section, body in document.items():
        if not section.startswith(args.prefix) or not isinstance(body, dict):
            continue
        values = body.get("sql", [])
        values = [values] if isinstance(values, str) else values
        for index, sql in enumerate(values):
            sql = str(sql).strip().rstrip(";")
            if re.match(r"(?is)^select\b", sql):
                statements[f"{section}[{index}]"] = sql.replace("?", "1")
    config = {"host": "127.0.0.1", "port": args.port, "user": "root",
              "password": "CHANGE_ME", "database": "hybench_10x", "query_timeout_sec": 30}
    controller = MySQLCostAdapter(MySQLCLIClient(config))
    snapshot = controller.snapshot()
    endpoints = {
        "row_high": {"row_evaluate_cost": 100, "io_block_read_cost": 1},
        "row_low": {"row_evaluate_cost": .001, "io_block_read_cost": 1},
        "io_high": {"row_evaluate_cost": .1, "io_block_read_cost": 100},
        "io_low": {"row_evaluate_cost": .1, "io_block_read_cost": .001},
        "key_high": {"key_compare_cost": 100},
        "key_low": {"key_compare_cost": .0001},
        "memory_block_high": {"memory_block_read_cost": 100},
        "memory_block_low": {"memory_block_read_cost": .0001},
        "memory_temp_create_high": {"memory_temptable_create_cost": 1000},
        "memory_temp_create_low": {"memory_temptable_create_cost": .001},
        "memory_temp_row_high": {"memory_temptable_row_cost": 100},
        "memory_temp_row_low": {"memory_temptable_row_cost": .0001},
        "disk_temp_create_high": {"disk_temptable_create_cost": 1000},
        "disk_temp_create_low": {"disk_temptable_create_cost": .001},
        "disk_temp_row_high": {"disk_temptable_row_cost": 100},
        "disk_temp_row_low": {"disk_temptable_row_cost": .0001},
    }
    results = {}
    try:
        for label, costs in endpoints.items():
            controller.restore(snapshot)
            controller.apply(costs)
            rows = {}
            with ThreadPoolExecutor(max_workers=8) as pool:
                pending = {pool.submit(MySQLCLIClient(config).execute, "EXPLAIN FORMAT=JSON " + sql): name
                           for name, sql in statements.items()}
                for future in as_completed(pending):
                    name = pending[future]
                    try:
                        plan = canonical(json.loads(future.result()))
                        value = json.dumps(plan, sort_keys=True, separators=(",", ":"))
                        rows[name] = {"fingerprint": hashlib.sha256(value.encode()).hexdigest(), "error": None}
                    except Exception as exc:
                        rows[name] = {"fingerprint": None, "error": str(exc)}
            results[label] = rows
    finally:
        controller.restore(snapshot)
    changed = []
    for name, sql in statements.items():
        fingerprints = {label: results[label][name]["fingerprint"] for label in endpoints}
        if len({value for value in fingerprints.values() if value}) > 1:
            changed.append({"statement": name, "sql": sql, "fingerprints": fingerprints})
    payload = {"statement_count": len(statements), "changed_count": len(changed), "changed": changed,
               "errors": {label: {name: row["error"] for name, row in rows.items() if row["error"]}
                          for label, rows in results.items()}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"statement_count": len(statements), "changed": [row["statement"] for row in changed]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
