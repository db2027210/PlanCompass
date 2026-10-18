#!/usr/bin/env python3
"""Audit structural JOB/IMDB plans for all ten experience points."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
from mysql_preflight_run import _plan_fingerprint


def access_sequence(value):
    found = []
    if isinstance(value, dict):
        if "table_name" in value:
            found.append({
                "table": value.get("table_name"),
                "access_type": value.get("access_type"),
                "key": value.get("key"),
            })
        for item in value.values():
            found.extend(access_sequence(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(access_sequence(item))
    return found


def main() -> int:
    source = json.loads((ROOT / "preflight/experience_points_20260721.json").read_text())
    points = [item for item in source["points"] if item["benchmark"] == "job"]
    query_path = Path("/home/anonymous/dataset/pg_data/join-order-benchmark/9b.sql")
    query = query_path.read_text(encoding="utf-8").strip().rstrip(";")
    client = MySQLCLIClient({
        "host": "127.0.0.1", "port": 13412, "user": "root",
        "password": "CHANGE_ME", "database": "imdb_job", "query_timeout_sec": 120,
    })
    adapter = MySQLCostAdapter(client)
    snapshot = adapter.snapshot()
    rows = []
    try:
        for point in points:
            adapter.apply(point["values"])
            raw = client.execute("EXPLAIN FORMAT=JSON " + query).strip()
            parsed = json.loads(raw)
            rows.append({
                "point_id": point["point_id"],
                "structural_plan_fingerprint": _plan_fingerprint([raw]),
                "access_sequence": access_sequence(parsed),
            })
    finally:
        adapter.restore(snapshot)
    payload = {
        "benchmark": "job", "query": str(query_path), "points": rows,
        "distinct_structural_plans": len({item["structural_plan_fingerprint"] for item in rows}),
    }
    output = ROOT / "preflight/full_20260721/job_plan_audit.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, output)
    print(json.dumps({"output": str(output), "distinct_structural_plans": payload["distinct_structural_plans"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
