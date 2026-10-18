#!/usr/bin/env python3
"""Audit the repeatability floor of resource-only Algorithm C labels."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np

from analyze_algorithm_c_resource_model_families import grouped_tail_split
from build_algorithm_c_experience_table_from_replays import (
    load_samples,
    prediction_metrics,
)


RESOURCES = ("cpu", "memory", "io")
DEADBAND = math.log(1.01)


def transition_identity(
    sample: Mapping[str, Any], active_params: Sequence[str]
) -> Tuple[str, ...]:
    params = sample.get("params") or {}
    x = sample.get("x") or []
    candidate = [float(params[name]) for name in active_params]
    baseline = [
        candidate[index] / math.exp(float(x[index]))
        for index in range(len(active_params))
    ]
    plan = sample.get("plan_prediction") or {}
    fingerprint = str(plan.get("workload_plan_fingerprint") or "")
    return (
        *[format(value, ".12g") for value in baseline],
        *[format(value, ".12g") for value in candidate],
        fingerprint,
    )


def sign(value: float) -> int:
    if value <= -DEADBAND:
        return -1
    if value >= DEADBAND:
        return 1
    return 0


def audit(
    fit: Sequence[Dict[str, Any]],
    validation: Sequence[Dict[str, Any]],
    holdout: Sequence[Dict[str, Any]],
    active_params: Sequence[str],
) -> Dict[str, Any]:
    preholdout = [*fit, *validation]
    by_identity: Dict[Tuple[str, ...], List[Dict[str, Any]]] = defaultdict(list)
    for row in preholdout:
        by_identity[transition_identity(row, active_params)].append(row)

    repeated = [rows for rows in by_identity.values() if len(rows) >= 2]
    conflicts: Dict[str, int] = {resource: 0 for resource in RESOURCES}
    repeated_material: Dict[str, int] = {resource: 0 for resource in RESOURCES}
    for rows in repeated:
        for resource in RESOURCES:
            signs = {sign(float(row["outcomes"][resource])) for row in rows}
            material = signs.difference({0})
            if material:
                repeated_material[resource] += 1
            if material == {-1, 1}:
                conflicts[resource] += 1

    exact_predictions: Dict[str, List[float]] = {
        resource: [] for resource in RESOURCES
    }
    exact_actual: Dict[str, List[float]] = {resource: [] for resource in RESOURCES}
    exact_labels: List[str] = []
    for row in holdout:
        matches = by_identity.get(transition_identity(row, active_params)) or []
        if not matches:
            continue
        exact_labels.append(str(row.get("label") or ""))
        for resource in RESOURCES:
            exact_predictions[resource].append(
                float(np.median([m["outcomes"][resource] for m in matches]))
            )
            exact_actual[resource].append(float(row["outcomes"][resource]))

    exact_metrics: Dict[str, Any] = {}
    for resource in RESOURCES:
        if len(exact_actual[resource]) >= 2:
            exact_metrics[resource] = prediction_metrics(
                exact_actual[resource], exact_predictions[resource]
            )
        else:
            exact_metrics[resource] = None
    return {
        "contract": "algorithm_c_exact_transition_repeatability_floor_v1",
        "qps_used": False,
        "accept_reject_used": False,
        "deadband_log_ratio": DEADBAND,
        "preholdout_count": len(preholdout),
        "unique_transition_count": len(by_identity),
        "repeated_transition_count": len(repeated),
        "repeated_material_transition_count": repeated_material,
        "opposite_material_direction_conflict_count": conflicts,
        "holdout_count": len(holdout),
        "holdout_exact_transition_match_count": len(exact_labels),
        "holdout_exact_transition_match_labels": exact_labels,
        "exact_transition_holdout_metrics": exact_metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="append", required=True, type=Path)
    parser.add_argument("--source-algorithm", action="append", required=True)
    parser.add_argument("--active-params", required=True)
    parser.add_argument("--holdout-count", type=int, default=30)
    parser.add_argument("--validation-count", type=int, default=30)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if len(args.history) != len(args.source_algorithm):
        raise ValueError("history/source algorithm counts differ")
    active_params = [value.strip() for value in args.active_params.split(",")]
    samples, source_audit = load_samples(
        args.history,
        active_params,
        source_algorithms={
            str(path): algorithm
            for path, algorithm in zip(args.history, args.source_algorithm)
        },
    )
    fit, validation, holdout = grouped_tail_split(
        samples,
        holdout_count=max(10, args.holdout_count),
        validation_count=max(10, args.validation_count),
    )
    payload = audit(fit, validation, holdout, active_params)
    payload["source_audit"] = source_audit
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(str(args.output), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
