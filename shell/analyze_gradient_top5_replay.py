#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, List


def _average_ranks(values: List[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for offset in range(start, end):
            ranks[order[offset]] = rank
        start = end
    return ranks


def _spearman(left: List[float], right: List[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    x = _average_ranks(left)
    y = _average_ranks(right)
    x_mean = sum(x) / len(x)
    y_mean = sum(y) / len(y)
    numerator = sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(x, y)
    )
    x_norm = math.sqrt(sum((value - x_mean) ** 2 for value in x))
    y_norm = math.sqrt(sum((value - y_mean) ** 2 for value in y))
    if x_norm == 0.0 or y_norm == 0.0:
        return None
    return numerator / (x_norm * y_norm)


def analyze(summary: Dict[str, Any], capability: Dict[str, float]) -> Dict[str, Any]:
    rows = list(summary.get("successful") or [])
    resources = ("cpu", "memory", "io")
    predicted_scores = [float(row["predicted_score"]) for row in rows]
    actual_scores = [float(row["actual_score"]) for row in rows]
    per_candidate = []
    absolute_resource_errors = {name: [] for name in resources}
    for row in rows:
        predicted_utilization = {
            name: float(row["predicted_resource"][name]) / capability[name]
            for name in resources
        }
        actual_utilization = {
            name: float(row["actual_utilization"][name])
            for name in resources
        }
        resource_error_pp = {
            name: 100.0
            * (predicted_utilization[name] - actual_utilization[name])
            for name in resources
        }
        for name in resources:
            absolute_resource_errors[name].append(abs(resource_error_pp[name]))
        per_candidate.append(
            {
                "predicted_rank": int(row["rank"]),
                "predicted_score": float(row["predicted_score"]),
                "actual_score": float(row["actual_score"]),
                "score_error": (
                    float(row["predicted_score"]) - float(row["actual_score"])
                ),
                "predicted_utilization": predicted_utilization,
                "actual_utilization": actual_utilization,
                "resource_error_pp": resource_error_pp,
                "algorithmic_gate_rejection": bool(
                    row.get("algorithmic_gate_rejection")
                ),
            }
        )
    actual_order = [
        int(row["rank"])
        for row in sorted(rows, key=lambda item: float(item["actual_score"]), reverse=True)
    ]
    best = max(rows, key=lambda item: float(item["actual_score"])) if rows else None
    return {
        "successful_real_replay_count": len(rows),
        "technical_failure_count": int(summary.get("technical_failure_count", 0)),
        "baseline_actual_score": summary.get("baseline_actual_score"),
        "score_mae": (
            sum(abs(predicted - actual) for predicted, actual in zip(
                predicted_scores,
                actual_scores,
            ))
            / len(rows)
            if rows
            else None
        ),
        "resource_mae_pp": {
            name: (
                sum(values) / len(values)
                if values
                else None
            )
            for name, values in absolute_resource_errors.items()
        },
        "predicted_score_spread": (
            max(predicted_scores) - min(predicted_scores)
            if predicted_scores
            else None
        ),
        "actual_score_spread": (
            max(actual_scores) - min(actual_scores)
            if actual_scores
            else None
        ),
        "spearman": _spearman(predicted_scores, actual_scores),
        "actual_best_predicted_rank": int(best["rank"]) if best else None,
        "actual_rank_order_by_predicted_rank": actual_order,
        "selected_incumbent_rank": summary.get("selected_incumbent_rank"),
        "incumbent_updated": bool(summary.get("incumbent_updated")),
        "baseline_params_restored": bool(summary.get("baseline_params_restored")),
        "baseline_snapshot_restored": bool(summary.get("baseline_snapshot_restored")),
        "per_candidate": per_candidate,
    }


def main() -> None:
    if len(sys.argv) not in {3, 4}:
        raise SystemExit(
            "usage: analyze_gradient_top5_replay.py SUMMARY CONFIG [OUTPUT]"
        )
    summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    config = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    capability = {
        name: float(value)
        for name, value in (config.get("capability") or {}).items()
    }
    report = analyze(summary, capability)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if len(sys.argv) == 4:
        Path(sys.argv[3]).write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
