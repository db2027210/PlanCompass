#!/usr/bin/env python3
"""Time a candidate JOB query across the ten cost points before a full rerun."""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="9b.sql")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    source = json.loads((ROOT / "preflight/experience_points_20260721.json").read_text())
    points = [item for item in source["points"] if item["benchmark"] == "job"]
    path = Path("/home/anonymous/dataset/pg_data/join-order-benchmark") / args.query
    sql = path.read_text(encoding="utf-8").strip().rstrip(";")
    statement = "SET SESSION MAX_EXECUTION_TIME=15000; " + sql
    client = MySQLCLIClient({
        "host": "127.0.0.1", "port": 13412, "user": "root",
        "password": "CHANGE_ME", "database": "imdb_job", "query_timeout_sec": 20,
    })
    adapter = MySQLCostAdapter(client)
    snapshot = adapter.snapshot()
    rows = []
    try:
        for point in points:
            adapter.apply(point["values"])
            durations = []
            errors = []
            for repeat in range(args.repeats + 1):
                started = time.monotonic()
                try:
                    client.execute(statement)
                    elapsed = time.monotonic() - started
                    if repeat > 0:
                        durations.append(elapsed)
                except Exception as exc:
                    errors.append(str(exc))
            rows.append({
                "point_id": point["point_id"], "durations_sec": durations,
                "median_sec": statistics.median(durations) if durations else None,
                "errors": errors,
            })
    finally:
        adapter.restore(snapshot)
    payload = {"query": args.query, "repeats": args.repeats, "points": rows}
    output = ROOT / f"preflight/full_20260721/job_{path.stem}_timing_pilot.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, output)
    medians = [item["median_sec"] for item in rows if item["median_sec"] is not None]
    print(json.dumps({
        "output": str(output), "successful_points": len(medians),
        "min_sec": min(medians, default=None), "max_sec": max(medians, default=None),
        "spread_ratio": max(medians) / min(medians) if medians else None,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
