#!/usr/bin/env python3
"""Score Grok blind resource-direction predictions against hidden truth."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Mapping


ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS = ROOT / "docs/artifacts/grok_resource_direction_blind_predictions_20260723.json"
TRUTH = ROOT / "docs/artifacts/grok_resource_direction_blind_truth_20260723.json"
REPORT = ROOT / "docs/artifacts/grok_resource_direction_blind_evaluation_20260723.json"
REPORT_MD = ROOT / "docs/grok_resource_direction_blind_evaluation_20260723.md"
RESOURCES = ("cpu", "memory", "io")


def load_prediction(path: Path) -> Mapping[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, Mapping):
        raise ValueError("prediction output must be a JSON object")
    if isinstance(value.get("text"), str):
        value = json.loads(str(value["text"]))
        if not isinstance(value, Mapping):
            raise ValueError("structured prediction text must contain a JSON object")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, default=PREDICTIONS)
    parser.add_argument("--truth", type=Path, default=TRUTH)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--report-md", type=Path, default=REPORT_MD)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    predicted_document = load_prediction(args.predictions)
    truth_document = json.loads(args.truth.read_text(encoding="utf-8"))
    if int(truth_document.get("schema_version") or 1) >= 2:
        normalized_truth = []
        for row in truth_document["truth"]:
            normalized_truth.append(
                {
                    **row,
                    "direction": {
                        resource: (row.get("resource_labels") or {}).get(resource, {}).get("direction", "unknown")
                        for resource in RESOURCES
                    },
                }
            )
        truth_document = {**truth_document, "truth": normalized_truth}
    predictions = {
        str(row["case_id"]): row
        for row in predicted_document.get("cases") or []
        if isinstance(row, Mapping) and row.get("case_id")
    }
    truth = {str(row["case_id"]): row for row in truth_document["truth"]}
    missing_case_ids = sorted(set(truth) - set(predictions))
    unexpected_case_ids = sorted(set(predictions) - set(truth))
    protocol_valid = not missing_case_ids and not unexpected_case_ids
    counts: Dict[str, Dict[str, int]] = {
        resource: {
            "total": 0,
            "known": 0,
            "correct_known": 0,
            "high_confidence": 0,
            "correct_high_confidence": 0,
            "ambiguous_truth_excluded": 0,
        }
        for resource in RESOURCES
    }
    details = []
    pruning_safe_count = 0
    unsafe_pruning_count = 0
    for case_id, expected in truth.items():
        predicted = predictions.get(case_id) or {}
        predicted_directions = predicted.get("direction") or {}
        confidence = predicted.get("confidence") or {}
        correct_all = True
        resource_details = {}
        for resource in RESOURCES:
            actual = str(expected["direction"][resource])
            guess = str(predicted_directions.get(resource) or "unknown")
            try:
                confidence_value = float(confidence.get(resource) or 0.0)
            except (TypeError, ValueError):
                confidence_value = 0.0
            correct = guess == actual
            if actual == "unknown":
                correct_all = False
                counts[resource]["ambiguous_truth_excluded"] += 1
                resource_details[resource] = {
                    "expected": actual,
                    "predicted": guess,
                    "confidence": confidence_value,
                    "correct": None,
                    "excluded_reason": "ambiguous_truth",
                }
                continue
            correct_all = correct_all and correct
            row = counts[resource]
            row["total"] += 1
            if guess != "unknown":
                row["known"] += 1
                row["correct_known"] += int(correct)
            if guess != "unknown" and confidence_value >= 0.70:
                row["high_confidence"] += 1
                row["correct_high_confidence"] += int(correct)
            resource_details[resource] = {
                "expected": actual,
                "predicted": guess,
                "confidence": confidence_value,
                "correct": correct,
            }
        pruning_safe = predicted.get("pruning_safe") is True
        if pruning_safe:
            pruning_safe_count += 1
            unsafe_pruning_count += int(not correct_all)
        details.append(
            {
                "case_id": case_id,
                "resources": resource_details,
                "pruning_safe": pruning_safe,
                "all_directions_correct": correct_all,
            }
        )
    metrics: Dict[str, Any] = {}
    total_high_confidence = 0
    total_correct_high_confidence = 0
    for resource, row in counts.items():
        known = row["known"]
        high = row["high_confidence"]
        total_high_confidence += high
        total_correct_high_confidence += row["correct_high_confidence"]
        metrics[resource] = {
            **row,
            "known_coverage": known / row["total"] if row["total"] else 0.0,
            "known_accuracy": row["correct_known"] / known if known else None,
            "high_confidence_accuracy": (
                row["correct_high_confidence"] / high if high else None
            ),
        }
    aggregate_high_accuracy = (
        total_correct_high_confidence / total_high_confidence
        if total_high_confidence
        else None
    )
    passed = bool(
        protocol_valid
        and
        total_high_confidence >= 10
        and aggregate_high_accuracy is not None
        and aggregate_high_accuracy >= 0.80
        and unsafe_pruning_count == 0
    )
    report = {
        "schema_version": 1,
        "case_count": len(truth),
        "resource_direction_count": len(truth) * len(RESOURCES),
        "metrics": metrics,
        "aggregate_high_confidence_count": total_high_confidence,
        "aggregate_high_confidence_accuracy": aggregate_high_accuracy,
        "pruning_safe_count": pruning_safe_count,
        "unsafe_pruning_count": unsafe_pruning_count,
        "protocol_valid": protocol_valid,
        "missing_case_ids": missing_case_ids,
        "unexpected_case_ids": unexpected_case_ids,
        "admission": {
            "passed": passed,
            "requirements": {
                "min_high_confidence_directions": 10,
                "min_high_confidence_accuracy": 0.80,
                "max_unsafe_pruning_count": 0,
            },
            "scope": "historical parameter-delta experience only; no plan-feature claim",
        },
        "details": details,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Grok PostgreSQL",
        "",
        f"- {len(truth)}",
        f"- {len(truth) * len(RESOURCES)}",
        f"- {total_high_confidence}",
        f"- {aggregate_high_accuracy}",
        f"- Agent{pruning_safe_count}",
        f"- {unsafe_pruning_count}",
        f"- {protocol_valid}",
        f"- case{missing_case_ids}",
        f"- case{unexpected_case_ids}",
        f"- {passed}",
        "",
        "scopeAgent",
    ]
    args.report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report["admission"], ensure_ascii=False), flush=True)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
