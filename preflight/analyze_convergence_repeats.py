#!/usr/bin/env python3
"""Verify that selected MySQL preflight optima repeat as stable convergence points."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean, pstdev

BENCHMARKS = ("hybench_ap", "job", "tpch", "hybench_tp", "tpcc", "hybench_xp")


def point_qps(item):
    raw = item.get("resource_raw", {})
    count = raw.get("query_count")
    duration = raw.get("query_time") or item.get("measurement_duration_sec")
    if count is not None and duration is not None and float(count) > 0.0 and float(duration) > 0.0:
        return float(count) / float(duration)
    return float(item["qps"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep-summary", type=Path, default=Path("preflight/full_20260721/summary.json"))
    parser.add_argument("--directory", type=Path, default=Path("preflight/convergence_20260721"))
    parser.add_argument("--output", type=Path, default=Path("preflight/convergence_20260721/verification.json"))
    args = parser.parse_args()
    sweep = json.loads(args.sweep_summary.read_text(encoding="utf-8"))
    sweep_by_benchmark = {item["benchmark"]: item for item in sweep["benchmarks"]}
    rows = []
    for benchmark in BENCHMARKS:
        path = args.directory / f"{benchmark}.json"
        points = []
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            points = [item for item in payload.get("points", []) if not item.get("error")]
        qps = [point_qps(item) for item in points]
        target = [float(item["target_utilization"]) for item in points]
        repeat_plans = [item.get("plan_fingerprint") for item in points]
        repeat_plan_consistent = bool(
            len(repeat_plans) == 2
            and all(repeat_plans)
            and len(set(repeat_plans)) == 1
        )
        qps_cv = pstdev(qps) / max(abs(mean(qps)), 1e-12) if len(qps) >= 2 else None
        target_span = max(target) - min(target) if len(target) >= 2 else None
        sweep_row = sweep_by_benchmark[benchmark]
        source_qps = float((sweep_row.get("selected_convergence_point") or {}).get("qps") or 0.0)
        source_qps_relative_error = (
            abs(mean(qps) - source_qps) / max(abs(source_qps), 1e-12)
            if len(qps) >= 2 and source_qps > 0.0 else None
        )
        passed = bool(
            len(points) == 2
            and all(float(item.get("qps") or 0.0) > 0.0 for item in points)
            and all(item.get("stable") for item in points)
            and all(item.get("bottleneck_constructed") for item in points)
            and qps_cv is not None and qps_cv <= 0.10
            and source_qps_relative_error is not None and source_qps_relative_error <= 0.10
            and target_span is not None and target_span <= 0.05
            and repeat_plan_consistent
            and sweep_row.get("tuning_leverage_observed")
        )
        rows.append({
            "benchmark": benchmark,
            "source_point_id": (sweep_row.get("selected_convergence_point") or {}).get("point_id"),
            "repeat_count": len(points),
            "qps_cv": qps_cv,
            "source_qps": source_qps,
            "repeat_mean_qps": mean(qps) if qps else None,
            "source_qps_relative_error": source_qps_relative_error,
            "target_utilization_span": target_span,
            "both_stable": len(points) == 2 and all(item.get("stable") for item in points),
            "both_constructed": len(points) == 2 and all(item.get("bottleneck_constructed") for item in points),
            "repeat_plan_consistent": repeat_plan_consistent,
            "repeat_plan_fingerprint": repeat_plans[0] if repeat_plan_consistent else None,
            "tuning_leverage_observed": bool(sweep_row.get("tuning_leverage_observed")),
            "convergence_verified": passed,
        })
    result = {"complete": all(item["convergence_verified"] for item in rows), "benchmarks": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"complete": result["complete"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
