#!/usr/bin/env python3
"""Audit HyBench XP AP-Q9 structural plans at all ten cost points."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
from mysql_preflight_run import _plan_fingerprint


QUERY = """
SELECT c.name, SUM(t.amount) AS total_revenue
FROM company c, transfer t
WHERE c.companyid=t.targetid
  AND EXISTS (
    SELECT * FROM loanapps la
    WHERE la.applicantid=c.companyid
      AND loanapps_ts BETWEEN STR_TO_DATE('2023-11-01','%Y-%m-%d')
      AND (STR_TO_DATE('2023-11-01','%Y-%m-%d') + INTERVAL 60 DAY)
  )
  AND NOT EXISTS (
    SELECT * FROM loantrans lt
    WHERE lt.applicantid=c.companyid
      AND loantrans_ts BETWEEN STR_TO_DATE('2023-11-01','%Y-%m-%d')
      AND (STR_TO_DATE('2023-11-01','%Y-%m-%d') + INTERVAL 60 DAY)
  )
GROUP BY c.name ORDER BY total_revenue DESC
""".strip()


def main() -> int:
    source = json.loads((ROOT / "preflight/experience_points_20260721.json").read_text())
    points = [item for item in source["points"] if item["benchmark"] == "hybench_xp"]
    # The completed AP container is schema/data-equivalent to the XP container
    # and avoids changing optimizer costs underneath the live XP sweep.
    client = MySQLCLIClient({
        "host": "127.0.0.1", "port": 13365, "user": "root",
        "password": "CHANGE_ME", "database": "hybench_10x", "query_timeout_sec": 120,
    })
    adapter = MySQLCostAdapter(client)
    snapshot = adapter.snapshot()
    rows = []
    try:
        for point in points:
            adapter.apply(point["values"])
            raw = client.execute("EXPLAIN FORMAT=JSON " + QUERY).strip()
            rows.append({
                "point_id": point["point_id"],
                "structural_plan_fingerprint": _plan_fingerprint([raw]),
            })
    finally:
        adapter.restore(snapshot)
    payload = {
        "benchmark": "hybench_xp", "query": "AP-9", "sql": QUERY, "points": rows,
        "distinct_structural_plans": len({item["structural_plan_fingerprint"] for item in rows}),
    }
    output = ROOT / "preflight/full_20260721/hybench_xp_plan_audit.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, output)
    print(json.dumps({"output": str(output), "distinct_structural_plans": payload["distinct_structural_plans"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
