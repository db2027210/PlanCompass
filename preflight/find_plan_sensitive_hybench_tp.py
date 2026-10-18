#!/usr/bin/env python3
"""Screen read-only HyBench TP statements for cost-sensitive plan changes."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
from mysql_preflight_run import _plan_fingerprint


QUERIES = {
    "tp5": (
        "SELECT * FROM transfer WHERE sourceid=1 OR targetid=1 "
        "ORDER BY transfer_ts DESC LIMIT 1"
    ),
    "tp6": (
        "SELECT * FROM checking WHERE sourceid=1 OR targetid=1 "
        "ORDER BY checking_ts DESC LIMIT 1"
    ),
    "tp7": (
        "SELECT * FROM loanapps WHERE applicantid=1 "
        "ORDER BY loanapps_ts DESC LIMIT 1"
    ),
    "tp8": (
        "SELECT * FROM loantrans WHERE applicantid=1 "
        "ORDER BY loantrans_ts DESC LIMIT 1"
    ),
    "tp13_read": (
        "SELECT id,applicantid,amount,duration,loanapps_ts FROM loanapps "
        "WHERE status='under_review' ORDER BY loanapps_ts LIMIT 1"
    ),
    "tp14_read": (
        "SELECT id,applicantid,amount,contract_timestamp FROM loantrans "
        "WHERE status='accept' ORDER BY loantrans_ts LIMIT 1"
    ),
    "tp15_read": (
        "SELECT id,applicantid,duration,loantrans_ts FROM loantrans "
        "WHERE status='lent' ORDER BY loantrans_ts LIMIT 1"
    ),
}


def main() -> int:
    source = json.loads((ROOT / "preflight/experience_points_20260721.json").read_text())
    points = [item for item in source["points"] if item["benchmark"] == "hybench_tp"]
    # Use the completed AP container as an isolated schema-equivalent plan
    # sandbox; this cannot perturb the running TP sweep container.
    client = MySQLCLIClient({
        "host": "127.0.0.1", "port": 13365, "user": "root",
        "password": "CHANGE_ME", "database": "hybench_10x", "query_timeout_sec": 120,
    })
    adapter = MySQLCostAdapter(client)
    snapshot = adapter.snapshot()
    scans = []
    try:
        for name, query in QUERIES.items():
            rows = []
            for point in points:
                adapter.apply(point["values"])
                raw = client.execute("EXPLAIN FORMAT=JSON " + query).strip()
                rows.append({
                    "point_id": point["point_id"],
                    "structural_plan_fingerprint": _plan_fingerprint([raw]),
                })
            scans.append({
                "query_name": name,
                "query": query,
                "distinct_structural_plans": len({item["structural_plan_fingerprint"] for item in rows}),
                "points": rows,
            })
    finally:
        adapter.restore(snapshot)
    payload = {"benchmark": "hybench_tp", "queries": scans}
    output = ROOT / "preflight/full_20260721/hybench_tp_plan_sensitivity_scan.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, output)
    print(json.dumps({
        "output": str(output),
        "plans": {item["query_name"]: item["distinct_structural_plans"] for item in scans},
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
