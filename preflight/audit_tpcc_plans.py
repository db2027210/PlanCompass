#!/usr/bin/env python3
"""Audit structural TPC-C StockLevel plans at all ten cost points."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
from mysql_preflight_run import _plan_fingerprint


STOCK_LEVEL_SQL = (
    "SELECT COUNT(DISTINCT s_i_id) FROM order_line, stock "
    "WHERE ol_w_id=1 AND ol_d_id=1 AND ol_o_id < 3001 "
    "AND ol_o_id >= 2981 AND s_w_id=1 AND s_i_id=ol_i_id "
    "AND s_quantity < 15"
)


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
    points = [item for item in source["points"] if item["benchmark"] == "tpcc"]
    client = MySQLCLIClient({
        "host": "127.0.0.1", "port": 57652, "user": "root",
        "password": "CHANGE_ME", "database": "tpcc", "query_timeout_sec": 120,
    })
    adapter = MySQLCostAdapter(client)
    snapshot = adapter.snapshot()
    rows = []
    try:
        for point in points:
            adapter.apply(point["values"])
            raw = client.execute("EXPLAIN FORMAT=JSON " + STOCK_LEVEL_SQL).strip()
            parsed = json.loads(raw)
            rows.append({
                "point_id": point["point_id"],
                "structural_plan_fingerprint": _plan_fingerprint([raw]),
                "access_sequence": access_sequence(parsed),
            })
    finally:
        adapter.restore(snapshot)
    payload = {
        "benchmark": "tpcc", "query": STOCK_LEVEL_SQL, "points": rows,
        "distinct_structural_plans": len({item["structural_plan_fingerprint"] for item in rows}),
    }
    output = ROOT / "preflight/full_20260721/tpcc_plan_audit.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, output)
    print(json.dumps({"output": str(output), "distinct_structural_plans": payload["distinct_structural_plans"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
