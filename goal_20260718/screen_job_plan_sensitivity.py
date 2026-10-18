#!/usr/bin/env python3
"""Find official JOB queries whose MySQL plans change across cost endpoints."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter


def normalized_plan(value: Any) -> Any:
    if isinstance(value, list):
        return [normalized_plan(item) for item in value]
    if isinstance(value, dict):
        ignored = {
            "cost_info", "rows_examined_per_scan", "rows_produced_per_join",
            "filtered", "data_read_per_join",
        }
        return {key: normalized_plan(item) for key, item in value.items() if key not in ignored}
    return value


def fingerprint(raw: str) -> str:
    plan = normalized_plan(json.loads(raw))
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def plan_stats(raw: str) -> dict[str, float | int]:
    value = json.loads(raw)
    table_count = 0
    rows_examined = 0.0
    def visit(item: Any) -> None:
        nonlocal table_count, rows_examined
        if isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            if "table_name" in item:
                table_count += 1
                rows_examined += float(item.get("rows_examined_per_scan", 0) or 0)
            for child in item.values():
                visit(child)
    visit(value)
    return {"table_count": table_count, "rows_examined_per_scan_sum": rows_examined}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query-dir", type=Path, default=Path("/home/anonymous/dataset/pg_data/join-order-benchmark"))
    parser.add_argument("--output", type=Path, default=Path("screen_results/job_plan_sensitivity.json"))
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    paths = [path for path in args.query_dir.glob("*.sql") if re.match(r"^\d+[a-z]\.sql$", path.name)]
    paths.sort(key=lambda path: (int(re.match(r"\d+", path.name).group()), path.name))
    queries = {}
    for path in paths:
        sql = path.read_text(encoding="utf-8").strip().rstrip(";")
        if re.match(r"(?is)^select\b", sql):
            queries[path.name] = sql
    client_config = {
        "host": "127.0.0.1", "port": 13362, "user": "root", "password": "CHANGE_ME",
        "database": "job", "query_timeout_sec": 45,
    }
    controller = MySQLCostAdapter(MySQLCLIClient(client_config))
    snapshot = controller.snapshot()
    configurations = {
        "row_high": {"row_evaluate_cost": 100.0, "io_block_read_cost": 1.0},
        "row_low": {"row_evaluate_cost": 0.1, "io_block_read_cost": 1.0},
        "io_high": {"row_evaluate_cost": 0.1, "io_block_read_cost": 100.0},
        "io_low": {"row_evaluate_cost": 0.1, "io_block_read_cost": 0.01},
    }
    results: dict[str, dict[str, Any]] = {}
    try:
        for label, values in configurations.items():
            controller.apply(values)
            rows: dict[str, Any] = {}
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                pending = {
                    pool.submit(MySQLCLIClient(client_config).execute, f"EXPLAIN FORMAT=JSON {sql}"): name
                    for name, sql in queries.items()
                }
                for future in as_completed(pending):
                    name = pending[future]
                    try:
                        raw = future.result()
                        rows[name] = {"fingerprint": fingerprint(raw), **plan_stats(raw), "error": None}
                    except Exception as exc:
                        rows[name] = {"fingerprint": None, "table_count": None,
                                      "rows_examined_per_scan_sum": None, "error": str(exc)}
            results[label] = rows
    finally:
        controller.restore(snapshot)
    changed = []
    for name in queries:
        fingerprints = {label: results[label][name]["fingerprint"] for label in configurations}
        valid = {value for value in fingerprints.values() if value}
        if len(valid) > 1:
            changed.append({
                "query": name,
                "fingerprints": fingerprints,
                "max_table_count": max(results[label][name]["table_count"] for label in configurations),
                "max_rows_examined_per_scan_sum": max(
                    results[label][name]["rows_examined_per_scan_sum"] for label in configurations
                ),
            })
    changed.sort(key=lambda row: (row["max_rows_examined_per_scan_sum"], row["max_table_count"], row["query"]))
    payload = {
        "query_count": len(queries),
        "configurations": configurations,
        "changed_count": len(changed),
        "changed": changed,
        "errors": {
            label: {name: row["error"] for name, row in rows.items() if row["error"]}
            for label, rows in results.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"query_count": len(queries), "changed_count": len(changed),
                      "changed_queries": [row["query"] for row in changed]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
