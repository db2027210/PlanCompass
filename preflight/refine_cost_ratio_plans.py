#!/usr/bin/env python3
"""Refine plan-sensitive cost ratios for the SQL selected by boundary scan."""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mysql_cost_adapter import MYSQL_COST_KEYS, MySQLCLIClient, MySQLCostAdapter
from preflight.mysql_cost_boundary_plan_scan import (
    BOUNDS,
    CONNECTIONS,
    DEFAULTS,
    access_path,
    explain_one,
    fingerprint,
    load_queries,
    load_tpcc,
)


SELECTIONS = {
    # Memory target: AP-9 changes from indexed correlated subqueries to
    # materialized/full-scan alternatives when memory read cost rises.
    "hybench_ap": dict(query="AP-9", axis="memory_block_read_cost", target="memory"),
    # 29c has six plans in the boundary scan and a bounded ~13 s pilot runtime.
    "job": dict(query="29c.sql", axis="memory_block_read_cost", target="memory"),
    # Q14 changes the outer table and ALL/ref access path as row cost rises.
    "tpch": dict(query="Q14", axis="row_evaluate_cost", target="memory"),
    # IO target: AP-9 materializes a subquery at high physical-read cost.
    "hybench_xp": dict(query="AP-9", axis="io_block_read_cost", target="io"),
    # TPC-C NewOrder executes this stock UPDATE once per order line.  High row
    # cost switches PRIMARY range access to an index-merge intersection.
    "tpcc": dict(query="NewOrder/update_stock", axis="row_evaluate_cost", target="cpu"),
}


def log_levels(lower: float, upper: float, count: int = 10) -> list[float]:
    start = math.log(lower)
    step = (math.log(upper) - start) / (count - 1)
    return [math.exp(start + index * step) for index in range(count)]


def linear_levels(lower: float, upper: float, count: int = 10) -> list[float]:
    return [lower + index * (upper - lower) / (count - 1) for index in range(count)]


def variant_count(rows: list[dict[str, Any]]) -> int:
    return len({row["structural_plan_fingerprint"] for row in rows})


def main() -> int:
    outdir = ROOT / "preflight" / "cost_ratio_20260721"
    outdir.mkdir(parents=True, exist_ok=True)
    aggregate_points: list[dict[str, Any]] = []
    summaries = []
    for benchmark, selection in SELECTIONS.items():
        candidates = load_tpcc(include_dml=True) if benchmark == "tpcc" else load_queries(benchmark)
        query = next(
            item for item in candidates
            if item["query"] == selection["query"]
        )
        config = dict(CONNECTIONS[benchmark])
        config.update(host="127.0.0.1", user="root", query_timeout_sec=120)
        client = MySQLCLIClient(config)
        adapter = MySQLCostAdapter(client)
        snapshot = adapter.snapshot()
        axis = selection["axis"]
        rows: list[dict[str, Any]] = []
        search_trace: list[dict[str, Any]] = []

        def evaluate(levels: list[float], point_prefix: str) -> list[dict[str, Any]]:
            evaluated = []
            for index, level in enumerate(levels):
                values = dict(DEFAULTS)
                values[axis] = level
                adapter.apply(values)
                plan = explain_one(client, query["sql"])
                evaluated.append({
                    "point_id": f"{benchmark}-{point_prefix}-p{index}",
                    "benchmark": benchmark,
                    "target_bottleneck": selection["target"],
                    "selected_query": selection["query"],
                    "selected_query_share": 1.0,
                    "varied_cost": axis,
                    "varied_cost_value": level,
                    "values": values,
                    "multipliers": {
                        name: values[name] / DEFAULTS[name] for name in MYSQL_COST_KEYS
                    },
                    "structural_plan_fingerprint": fingerprint(plan),
                    "access_path": access_path(plan),
                })
            return evaluated

        try:
            initial_rows = evaluate(log_levels(*BOUNDS[axis]), "search")
            # When the wide ten-point scan finds one transition, bracket the
            # crossing geometrically and place the final ten points evenly in
            # a local [boundary/2, boundary*2] window.  This avoids spending
            # nine real measurements on the same plan.
            if variant_count(initial_rows) == 2:
                transition = next(
                    (left, right)
                    for left, right in zip(initial_rows, initial_rows[1:])
                    if left["structural_plan_fingerprint"] != right["structural_plan_fingerprint"]
                )
                left_value = float(transition[0]["varied_cost_value"])
                right_value = float(transition[1]["varied_cost_value"])
                left_plan = transition[0]["structural_plan_fingerprint"]
                for iteration in range(12):
                    middle = math.sqrt(left_value * right_value)
                    middle_row = evaluate([middle], f"bisect-{iteration}")[0]
                    search_trace.append({
                        "value": middle,
                        "fingerprint": middle_row["structural_plan_fingerprint"],
                    })
                    if middle_row["structural_plan_fingerprint"] == left_plan:
                        left_value = middle
                    else:
                        right_value = middle
                boundary = math.sqrt(left_value * right_value)
                local_lower = max(BOUNDS[axis][0], boundary / 2.0)
                local_upper = min(BOUNDS[axis][1], boundary * 2.0)
                rows = evaluate(linear_levels(local_lower, local_upper), "ratio")
                spacing = "10 linear levels in a local [boundary/2, boundary*2] crossing window"
                boundary_bracket = [left_value, right_value]
            else:
                rows = initial_rows
                for index, row in enumerate(rows):
                    row["point_id"] = f"{benchmark}-ratio-p{index}"
                spacing = "10 log-uniform levels across the approved absolute bounds"
                boundary_bracket = None

            for row in rows:
                aggregate_points.append({
                    key: row[key]
                    for key in (
                        "point_id", "benchmark", "target_bottleneck",
                        "selected_query", "selected_query_share", "varied_cost",
                        "varied_cost_value", "values", "multipliers",
                        "structural_plan_fingerprint",
                    )
                })
        finally:
            adapter.restore(snapshot)
        variants: dict[str, dict[str, Any]] = {}
        for row in rows:
            variant = variants.setdefault(row["structural_plan_fingerprint"], {
                "fingerprint": row["structural_plan_fingerprint"],
                "access_path": row["access_path"],
                "point_ids": [],
                "axis_values": [],
            })
            variant["point_ids"].append(row["point_id"])
            variant["axis_values"].append(row["varied_cost_value"])
        payload = {
            "benchmark": benchmark,
            "selected_query": selection["query"],
            "selected_query_share_for_measurement": 1.0,
            "target_bottleneck": selection["target"],
            "axis": axis,
            "fixed_costs": {name: value for name, value in DEFAULTS.items() if name != axis},
            "spacing": spacing,
            "boundary_bracket": boundary_bracket,
            "boundary_search_trace": search_trace,
            "distinct_structural_plans": len(variants),
            "variants": list(variants.values()),
            "points": rows,
            "restored_after_scan": True,
        }
        target = outdir / f"{benchmark}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)
        summary = {
            "benchmark": benchmark,
            "query": selection["query"],
            "axis": axis,
            "plans": len(variants),
            "output": str(target),
        }
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)

    points_payload = {
        "version": 4,
        "design": "one cost ratio at a time; ten log-uniform levels",
        "parameters": list(MYSQL_COST_KEYS),
        "defaults": DEFAULTS,
        "points": aggregate_points,
    }
    points_target = outdir / "measurement_points.json"
    temporary = points_target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(points_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, points_target)
    print(json.dumps({"completed": summaries, "measurement_points": str(points_target)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
