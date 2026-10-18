#!/usr/bin/env python3
"""Audit HyBench AP and TPC-H Q6 plans over all ten cost points."""
from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
from mysql_preflight_run import _plan_fingerprint


TPCH_Q6 = """
SELECT SUM(l_extendedprice*l_discount) AS revenue
FROM lineitem
WHERE l_shipdate >= DATE('1994-01-01')
  AND l_shipdate < DATE_ADD(DATE('1994-01-01'), INTERVAL 1 YEAR)
  AND l_discount BETWEEN 0.05 AND 0.07
  AND l_quantity < 24
""".strip()


def audit(benchmark, client_config, queries, output_name):
    source = json.loads((ROOT / "preflight/experience_points_20260721.json").read_text())
    points = [item for item in source["points"] if item["benchmark"] == benchmark]
    client = MySQLCLIClient(client_config)
    adapter = MySQLCostAdapter(client)
    snapshot = adapter.snapshot()
    rows = []
    try:
        for point in points:
            adapter.apply(point["values"])
            raw_plans = [
                client.execute("EXPLAIN FORMAT=JSON " + query.strip().rstrip(";")).strip()
                for query in queries
            ]
            rows.append({
                "point_id": point["point_id"],
                "structural_plan_fingerprint": _plan_fingerprint(raw_plans),
                "query_plan_fingerprints": [_plan_fingerprint([raw]) for raw in raw_plans],
            })
    finally:
        adapter.restore(snapshot)
    payload = {
        "benchmark": benchmark,
        "query_count": len(queries),
        "points": rows,
        "distinct_structural_plans": len({item["structural_plan_fingerprint"] for item in rows}),
        "distinct_plans_by_query": [
            len({item["query_plan_fingerprints"][index] for item in rows})
            for index in range(len(queries))
        ],
    }
    output = ROOT / "preflight/full_20260721" / output_name
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, output)
    return payload, output


def main() -> int:
    statements = tomllib.loads(
        Path("/home/anonymous/HyBench-2024-master/conf/stmt_mysql.toml").read_text(encoding="utf-8")
    )
    ap_ids = ("AP-10",)
    ap_queries = [statements[name]["sql"] for name in ap_ids]
    ap, ap_output = audit(
        "hybench_ap",
        {"host": "127.0.0.1", "port": 13365, "user": "root", "password": "CHANGE_ME",
         "database": "hybench_10x", "query_timeout_sec": 120},
        ap_queries,
        "hybench_ap_plan_audit.json",
    )
    tpch, tpch_output = audit(
        "tpch",
        {"host": "127.0.0.1", "port": 13307, "user": "root", "password": "CHANGE_ME",
         "database": "tpch", "query_timeout_sec": 120},
        [TPCH_Q6],
        "tpch_plan_audit.json",
    )
    print(json.dumps({
        "hybench_ap": {"output": str(ap_output), "plans": ap["distinct_structural_plans"],
                        "plans_by_query": ap["distinct_plans_by_query"]},
        "tpch": {"output": str(tpch_output), "plans": tpch["distinct_structural_plans"],
                 "plans_by_query": tpch["distinct_plans_by_query"]},
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
