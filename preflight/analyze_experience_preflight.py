#!/usr/bin/env python3
"""Summarize the six-lane MySQL experience-point preflight as it runs."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

BENCHMARKS = ("hybench_ap", "job", "tpch", "hybench_tp", "tpcc", "hybench_xp")


def finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def target_is_dominant(item: dict[str, Any]) -> bool:
    target = item.get("target_bottleneck")
    normalized = item.get("resource_norm", {})
    if not finite(normalized.get(target)):
        return False
    peers = [
        float(value)
        for name, value in normalized.items()
        if name in {"cpu", "memory"} and name != target and finite(value)
    ]
    return float(normalized[target]) >= max(peers, default=0.0)


def point_qps(item: dict[str, Any]) -> float | None:
    """Recover unrounded HyBench throughput from its count when available."""
    raw = item.get("resource_raw", {})
    count = raw.get("query_count")
    duration = raw.get("query_time") or item.get("measurement_duration_sec")
    if finite(count) and finite(duration) and float(count) > 0.0 and float(duration) > 0.0:
        return float(count) / float(duration)
    return float(item["qps"]) if finite(item.get("qps")) else None


def load_latest(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    latest: dict[str, dict[str, Any]] = {}
    for item in payload.get("points", []):
        if isinstance(item, dict) and item.get("point_id"):
            latest[str(item["point_id"])] = item
    return list(latest.values())


def summarize(directory: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    all_complete = True
    for benchmark in BENCHMARKS:
        points = load_latest(directory / f"{benchmark}.json")
        if benchmark in {"hybench_ap", "job", "tpch", "tpcc", "hybench_xp"}:
            audit_path = directory / f"{benchmark}_plan_audit.json"
            if audit_path.is_file():
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
                structural = {
                    item["point_id"]: item.get("structural_plan_fingerprint")
                    for item in audit.get("points", [])
                }
                for item in points:
                    if structural.get(item.get("point_id")):
                        item["plan_fingerprint"] = structural[item["point_id"]]
        good = [item for item in points if not item.get("error")]
        stable = [item for item in good if item.get("stable")]
        constructed = [
            item for item in stable
            if item.get("bottleneck_constructed")
            and finite(item.get("qps"))
            and float(item["qps"]) > 0.0
            and target_is_dominant(item)
        ]
        all_complete = all_complete and len(good) == 10
        qps_values = [value for item in good if (value := point_qps(item)) is not None]
        target_values = [
            float(item["target_utilization"])
            for item in good if finite(item.get("target_utilization"))
        ]
        qps_max = max(qps_values, default=0.0)
        candidates = []
        for item in constructed:
            qps = point_qps(item) or 0.0
            target = float(item["target_utilization"])
            non_target_over = sum(
                max(0.0, float(value) - 1.0)
                for name, value in item.get("resource_norm", {}).items()
                if name != item.get("target_bottleneck")
                and name in {"cpu", "memory"}
                and finite(value)
            )
            target_stability = (
                item.get("resource_raw", {}).get("stability", {})
                .get("metrics", {}).get(item.get("target_bottleneck"), {})
            )
            target_cv = float(target_stability.get("cv") or 0.0)
            target_drift = float(target_stability.get("relative_drift") or 0.0)
            score = (
                (qps / qps_max if qps_max > 0 else 0.0)
                - 2.0 * abs(target - 0.85)
                - 4.0 * non_target_over
                - 2.0 * target_cv
                - 0.5 * target_drift
            )
            candidates.append((score, item))
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        selected = dict(candidates[0][1]) if candidates else None
        if selected is not None:
            selected["qps"] = point_qps(selected)
        plans = {item.get("plan_fingerprint") for item in good if item.get("plan_fingerprint")}
        qps_spread_pct = (
            100.0 * (max(qps_values) - min(qps_values)) / max(median(qps_values), 1e-12)
            if len(qps_values) >= 2 else None
        )
        target_spread = (
            max(target_values) - min(target_values) if len(target_values) >= 2 else None
        )
        rows.append({
            "benchmark": benchmark,
            "target_bottleneck": next((item.get("target_bottleneck") for item in points), None),
            "expected_points": 10,
            "successful_points": len(good),
            "error_points": len(points) - len(good),
            "stable_points": len(stable),
            "constructed_points": len(constructed),
            "early_stopped_points": sum(bool(item.get("early_stopped")) for item in good),
            "qps_min": min(qps_values, default=None),
            "qps_median": median(qps_values) if qps_values else None,
            "qps_max": max(qps_values, default=None),
            "qps_spread_pct": qps_spread_pct,
            "target_util_min": min(target_values, default=None),
            "target_util_max": max(target_values, default=None),
            "target_util_spread": target_spread,
            "distinct_plan_fingerprints": len(plans),
            "tuning_leverage_observed": bool(
                len(plans) >= 2
                and (
                    (qps_spread_pct is not None and qps_spread_pct >= 5.0)
                    or (target_spread is not None and target_spread >= 0.05)
                )
            ),
            "selected_convergence_point": None if selected is None else {
                key: selected.get(key) for key in (
                    "point_id", "values", "qps", "resource_norm", "target_utilization",
                    "measurement_duration_sec", "stable", "plan_fingerprint",
                )
            },
        })
    return {"complete": all_complete, "directory": str(directory.resolve()), "benchmarks": rows}


def markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# MySQL 8 ",
        "",
        "| Benchmark |  | /10 |  |  |  |  |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary["benchmarks"]:
        selected = row["selected_convergence_point"] or {}
        lines.append(
            f"| {row['benchmark']} | {row['target_bottleneck'] or '-'} | "
            f"{row['successful_points']}/10 | {row['stable_points']} | "
            f"{row['constructed_points']} | {row['early_stopped_points']} | "
            f"{selected.get('point_id', '-')} |"
        )
    lines.extend([
        "",
        "Memory 70%CPU 80%IO 75%",
        " 85% QPS ",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("preflight/full_20260721"))
    parser.add_argument("--output-json", type=Path, default=Path("preflight/full_20260721/summary.json"))
    parser.add_argument("--output-md", type=Path, default=Path("preflight/full_20260721/SUMMARY.md"))
    args = parser.parse_args()
    result = summarize(args.directory)
    args.output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.output_md.write_text(markdown(result), encoding="utf-8")
    print(json.dumps({"complete": result["complete"], "output": str(args.output_json)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
