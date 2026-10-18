#!/usr/bin/env python3
"""Recompute the MySQL resource-model gates from OOF prediction evidence."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


ROOT = Path(__file__).resolve().parent
MIN_R2 = 0.0
TOP_K = 15
MAX_INVERSION_RATE = 0.10


def _load_rows(path: Path, *, benchmarks: Iterable[str] | None = None) -> List[Dict[str, str]]:
    allowed = set(benchmarks or [])
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if allowed:
        rows = [row for row in rows if row.get("benchmark") in allowed]
    return rows


def _r2(actual: Sequence[float], predicted: Sequence[float]) -> float:
    mean_actual = sum(actual) / len(actual)
    residual = sum((left - right) ** 2 for left, right in zip(actual, predicted))
    total = sum((value - mean_actual) ** 2 for value in actual)
    return 1.0 - residual / total if total > 0.0 else 0.0


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _errors(actual: Sequence[float], predicted: Sequence[float]) -> Dict[str, float]:
    absolute = [abs(left - right) for left, right in zip(actual, predicted)]
    squared = [(left - right) ** 2 for left, right in zip(actual, predicted)]
    relative = [
        error / abs(value)
        for value, error in zip(actual, absolute)
        if abs(value) > 1e-12
    ]
    return {
        "mae": sum(absolute) / len(absolute),
        "rmse": math.sqrt(sum(squared) / len(squared)),
        "median_absolute_error": _percentile(absolute, 0.5),
        "p95_absolute_error": _percentile(absolute, 0.95),
        "mape_percent": 100.0 * sum(relative) / len(relative) if relative else 0.0,
    }


def _top15(actual: Sequence[float], predicted: Sequence[float]) -> Dict[str, float | int]:
    # Python's sort is stable, matching the repository evaluator's predicted
    # prefix semantics. Predicted ties are not removed from the denominator.
    indices = sorted(range(len(predicted)), key=lambda index: -predicted[index])[:TOP_K]
    inversions = 0
    comparable_pairs = 0
    for offset, left in enumerate(indices):
        for right in indices[offset + 1 :]:
            if actual[left] == actual[right]:
                continue
            comparable_pairs += 1
            if actual[left] < actual[right]:
                inversions += 1
    rate = inversions / comparable_pairs if comparable_pairs else float("inf")
    return {
        "labeled_items": len(indices),
        "comparable_pairs": comparable_pairs,
        "inversions": inversions,
        "pairwise_inversion_rate": rate,
    }


def _verify(
    name: str,
    path: Path,
    actual_column: str,
    predicted_column: str,
    *,
    benchmarks: Iterable[str] | None = None,
) -> Tuple[str, Dict[str, object]]:
    rows = _load_rows(path, benchmarks=benchmarks)
    actual = [float(row[actual_column]) for row in rows]
    predicted = [float(row[predicted_column]) for row in rows]
    ranking = _top15(actual, predicted)
    score = _r2(actual, predicted)
    errors = _errors(actual, predicted)
    passed = bool(
        score >= MIN_R2
        and ranking["labeled_items"] == TOP_K
        and ranking["pairwise_inversion_rate"] <= MAX_INVERSION_RATE
    )
    return name, {
        "status": "pass" if passed else "fail",
        "evidence": str(path),
        "samples": len(rows),
        "r2": score,
        "errors": errors,
        "top15": ranking,
    }


def main() -> int:
    checks = dict(
        [
            _verify(
                "cpu",
                ROOT / "cpu/artifacts/mysql_cpu_oof_predictions.csv",
                "target_cpu",
                "oof_prediction",
            ),
            _verify(
                "memory",
                ROOT / "memory/artifacts/oof_predictions.csv",
                "target_memory_mb",
                "oof_prediction_memory_mb",
                benchmarks=("C", "CH"),
            ),
            _verify(
                "io",
                ROOT / "io/output/oof_candidate_predictions.csv",
                "actual_io_mb",
                "oof_prediction_mb",
            ),
            _verify(
                "io_strict_case_held_out",
                ROOT / "io/output/strict_context_rank_predictions.csv",
                "actual_io_mb",
                "strict_ranking_score_mb",
            ),
        ]
    )
    report = {
        "thresholds": {
            "min_held_out_r2": MIN_R2,
            "top_k": TOP_K,
            "max_top15_pairwise_inversion_rate": MAX_INVERSION_RATE,
        },
        "checks": checks,
        "all_passed": all(check["status"] == "pass" for check in checks.values()),
    }
    output_path = ROOT / "verification.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["all_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
