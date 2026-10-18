#!/usr/bin/env python3
"""Find JOB queries whose structural MySQL plan changes across the ten points."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
from mysql_preflight_run import _plan_fingerprint


def query_key(path: Path):
    match = re.match(r"(\d+)(.*)\.sql$", path.name)
    return (int(match.group(1)), match.group(2)) if match else (10**9, path.name)


def main() -> int:
    source = json.loads((ROOT / "preflight/experience_points_20260721.json").read_text())
    points = [item for item in source["points"] if item["benchmark"] == "job"]
    query_dir = Path("/home/anonymous/dataset/pg_data/join-order-benchmark")
    queries = {
        path.name: path.read_text(encoding="utf-8").strip().rstrip(";")
        for path in sorted(query_dir.glob("*.sql"), key=query_key)
    }
    client = MySQLCLIClient({
        "host": "127.0.0.1", "port": 13412, "user": "root",
        "password": "CHANGE_ME", "database": "imdb_job", "query_timeout_sec": 60,
    })
    adapter = MySQLCostAdapter(client)
    snapshot = adapter.snapshot()
    signatures = {name: {} for name in queries}
    errors = []
    try:
        for point in points:
            adapter.apply(point["values"])
            for name, sql in queries.items():
                try:
                    raw = client.execute("EXPLAIN FORMAT=JSON " + sql).strip()
                    signatures[name][point["point_id"]] = _plan_fingerprint([raw])
                except Exception as exc:
                    errors.append({"query": name, "point_id": point["point_id"], "error": str(exc)})
    finally:
        adapter.restore(snapshot)
    ranked = []
    for name, by_point in signatures.items():
        ranked.append({
            "query": name,
            "successful_points": len(by_point),
            "distinct_structural_plans": len(set(by_point.values())),
            "fingerprints": by_point,
        })
    ranked.sort(key=lambda item: (-item["distinct_structural_plans"], query_key(Path(item["query"]))))
    payload = {"queries": ranked, "errors": errors}
    output = ROOT / "preflight/full_20260721/job_plan_sensitivity_scan.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, output)
    print(json.dumps({
        "output": str(output),
        "queries": len(ranked),
        "errors": len(errors),
        "top": [{"query": item["query"], "plans": item["distinct_structural_plans"]} for item in ranked[:10]],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
