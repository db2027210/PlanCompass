#!/usr/bin/env python3
"""Build one auditable calibration artifact from a measured plan boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from plan_transition_calibration import (
    attribute_query_impact,
    calibration_decision,
    changed_query_impact,
    diff_query_plans,
    difference_gate_95,
    joint_improvement_margin,
)


RESOURCE_METRIC_KEYS = {
    "cpu": "cpu_utilization",
    "memory": "memory_utilization",
    "io": "io_utilization",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError(f"{path}:{line_number}: JSONL row is not an object")
        rows.append(value)
    return rows


def extract_plan_bundle(artifact: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    for key in ("exact_structural_plans", "plans", "query_plans"):
        value = artifact.get(key)
        if isinstance(value, dict) and value:
            return value
    if artifact and all(isinstance(value, dict) for value in artifact.values()):
        return artifact  # type: ignore[return-value]
    raise RuntimeError("plan artifact has no per-query structural plan bundle")


def resource_windows(summary: Mapping[str, Any], resource: str) -> List[float]:
    stability = summary.get("resource_stability") or {}
    blocks = stability.get("blocks") or []
    metric_key = RESOURCE_METRIC_KEYS[resource]
    if blocks:
        metrics = blocks[-1].get("metrics") or {}
        values = (metrics.get(metric_key) or {}).get("window_means")
        if isinstance(values, list):
            return [float(value) for value in values]
    windows = stability.get("windows") or []
    return [
        float((row.get("metric_means") or {})[metric_key])
        for row in windows
        if metric_key in (row.get("metric_means") or {})
    ]


def query_hash_map(measurement_dir: Path) -> Dict[str, str]:
    path = measurement_dir / "measurement_ap13_correctness_gate.json"
    if not path.is_file():
        return {}
    value = load_json(path)
    result = value.get("expected_query_hash_by_id") or {}
    return {str(key): str(item) for key, item in result.items()}


def build_impact(measurement_dir: Path, summary: Mapping[str, Any]) -> Dict[str, Any]:
    events_path = measurement_dir / "measurement_queries.jsonl"
    samples_path = measurement_dir / "measurement_resource_samples.jsonl"
    if not events_path.is_file() or not samples_path.is_file():
        return {
            "schema_version": 1,
            "query_count": 0,
            "mapped_event_count": 0,
            "queries": [],
            "resource_attribution": {
                "quality": "unavailable",
                "reason": "measurement query events or resource samples are missing",
            },
        }
    denominators = summary.get("resource_denominators") or {}
    return attribute_query_impact(
        load_jsonl(events_path),
        load_jsonl(samples_path),
        query_hash_by_id=query_hash_map(measurement_dir),
        io_read_cap_bps=denominators.get("io_read_bps"),
        io_write_cap_bps=denominators.get("io_write_bps"),
    )


def source_entry(path: Path) -> Dict[str, Any]:
    return {"path": str(path.resolve()), "sha256": sha256(path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before-plan", type=Path, required=True)
    parser.add_argument("--after-plan", type=Path, required=True)
    parser.add_argument("--before-measurement-dir", type=Path, required=True)
    parser.add_argument("--after-measurement-dir", type=Path, required=True)
    parser.add_argument("--before-distance", type=float, required=True)
    parser.add_argument("--after-distance", type=float, required=True)
    parser.add_argument("--target-resource", choices=tuple(RESOURCE_METRIC_KEYS), required=True)
    parser.add_argument("--minimum-changed-query-time-share", type=float, default=0.05)
    parser.add_argument("--hard-gate-passed", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError(f"immutable calibration output already exists: {args.output}")
    if not 0.0 <= args.minimum_changed_query_time_share <= 1.0:
        raise ValueError("minimum changed-query time share must be in [0, 1]")

    before_summary_path = args.before_measurement_dir / "measurement_summary.json"
    after_summary_path = args.after_measurement_dir / "measurement_summary.json"
    before_summary = load_json(before_summary_path)
    after_summary = load_json(after_summary_path)
    before_bundle = extract_plan_bundle(load_json(args.before_plan))
    after_bundle = extract_plan_bundle(load_json(args.after_plan))
    plan_diff = diff_query_plans(before_bundle, after_bundle)
    before_impact = build_impact(args.before_measurement_dir, before_summary)
    after_impact = build_impact(args.after_measurement_dir, after_summary)
    changed_impact = changed_query_impact(plan_diff, after_impact)
    before_windows = resource_windows(before_summary, args.target_resource)
    after_windows = resource_windows(after_summary, args.target_resource)
    target_difference = difference_gate_95(before_windows, after_windows)

    before_utilization = before_summary["resource_mean_utilization"]
    after_utilization = after_summary["resource_mean_utilization"]
    target_before_pct = 100.0 * float(before_utilization[args.target_resource])
    target_after_pct = 100.0 * float(after_utilization[args.target_resource])
    non_target = {
        name: 100.0 * float(value)
        for name, value in after_utilization.items()
        if name != args.target_resource
    }
    before_error_pct = (
        100.0 * float(target_difference.before.half_width_95)
        if target_difference.before.half_width_95 is not None
        else None
    )
    after_error_pct = (
        100.0 * float(target_difference.after.half_width_95)
        if target_difference.after.half_width_95 is not None
        else None
    )
    joint = joint_improvement_margin(
        incumbent_qps=float(before_summary["db_qps"]),
        candidate_qps=float(after_summary["db_qps"]),
        incumbent_target=target_before_pct,
        candidate_target=target_after_pct,
        hard_gate_passed=args.hard_gate_passed,
        candidate_non_target=non_target,
        incumbent_target_error=before_error_pct,
        candidate_target_error=after_error_pct,
    )
    decision = calibration_decision(
        plan_diff=plan_diff,
        changed_impact=changed_impact,
        target_difference=target_difference,
        before_distance=args.before_distance,
        after_distance=args.after_distance,
        minimum_changed_query_time_share=args.minimum_changed_query_time_share,
    )
    artifact = {
        "schema_version": 1,
        "status": "learnable" if decision["learnable"] else "not_learnable",
        "target_resource": args.target_resource,
        "plan_diff": plan_diff,
        "before_query_impact": before_impact,
        "after_query_impact": after_impact,
        "changed_query_impact": changed_impact,
        "target_error_gate": {
            "before": target_difference.before.__dict__,
            "after": target_difference.after.__dict__,
            "delta_after_minus_before": target_difference.delta_after_minus_before,
            "combined_error_bound": target_difference.combined_error_bound,
            "credible": target_difference.credible,
            "direction": target_difference.direction,
            "reason": target_difference.reason,
        },
        "joint_improvement": joint,
        "calibration_decision": decision,
        "source": {
            "before_plan": source_entry(args.before_plan),
            "after_plan": source_entry(args.after_plan),
            "before_summary": source_entry(before_summary_path),
            "after_summary": source_entry(after_summary_path),
            "before_measurement_dir": str(args.before_measurement_dir.resolve()),
            "after_measurement_dir": str(args.after_measurement_dir.resolve()),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps({"status": artifact["status"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
