#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence


REQUIRED_CANDIDATE_FIELDS = (
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
)
RESOURCES = ("cpu", "io", "memory")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_observations(path: Path, event_name: str | None) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = [
            json.loads(line)
            for line in text.splitlines()
            if line.strip()
        ]
    if isinstance(payload, Mapping):
        records: Iterable[Any] = (
            payload.get("successful")
            or payload.get("results")
            or payload.get("observations")
            or []
        )
    elif isinstance(payload, list):
        records = payload
    else:
        raise ValueError("observations must be JSON, JSONL, or a ledger object")
    result = []
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        if event_name is not None and raw.get("event") != event_name:
            continue
        result.append(dict(raw))
    return result


def finite(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} is not finite")
    return parsed


def average_ranks(values: Sequence[float], *, reverse: bool) -> List[float]:
    order = sorted(
        range(len(values)),
        key=lambda index: values[index],
        reverse=reverse,
    )
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for index in order[start:end]:
            ranks[index] = rank
        start = end
    return ranks


def pearson(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("correlation requires equal sequences of length >= 2")
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean)
        for x, y in zip(left, right)
    )
    left_norm = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_norm = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def score(record: Mapping[str, Any], candidate: Mapping[str, Any]) -> tuple[float, float]:
    predicted = finite(
        record.get("predicted_score", candidate.get("candidate_score")),
        "predicted_score",
    )
    actual = finite(record.get("actual_score"), "actual_score")
    return predicted, actual


def candidate_role(candidate: Mapping[str, Any]) -> str:
    role = str(candidate.get("selection_role") or "")
    return "probe" if role in {"probe", "uncertain_probe"} else role


def analyze(
    selection: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    candidates = selection.get("top5") or selection.get("candidates") or []
    if not isinstance(candidates, list) or len(candidates) != 5:
        raise ValueError("selection must contain exactly five candidates")
    roles = [candidate_role(candidate) for candidate in candidates]
    missing_fields = {
        rank: [field for field in REQUIRED_CANDIDATE_FIELDS if field not in candidate]
        for rank, candidate in enumerate(candidates, 1)
        if any(field not in candidate for field in REQUIRED_CANDIDATE_FIELDS)
    }
    fingerprints = [
        str((candidate.get("three_level_fingerprint") or {}).get("id") or "")
        for candidate in candidates
    ]
    by_rank = {
        int(record["rank"]): record
        for record in observations
        if record.get("rank") is not None
        and record.get("technical_failure") is not True
        and record.get("valid_replay") is not False
    }
    failures = [
        record
        for record in observations
        if record.get("technical_failure") is True
    ]
    trusted_ranks = [rank for rank, role in enumerate(roles, 1) if role == "trusted"]
    trusted_pairs = [
        score(by_rank[rank], candidates[rank - 1])
        for rank in trusted_ranks
        if rank in by_rank
    ]
    trusted_predicted = [pair[0] for pair in trusted_pairs]
    trusted_actual = [pair[1] for pair in trusted_pairs]
    spearman = (
        pearson(
            average_ranks(trusted_predicted, reverse=True),
            average_ranks(trusted_actual, reverse=True),
        )
        if len(trusted_pairs) >= 2
        else None
    )
    errors = [
        actual - predicted
        for predicted, actual in trusted_pairs
    ]
    replayed_pairs = [
        (
            rank,
            *score(by_rank[rank], candidates[rank - 1]),
        )
        for rank in range(1, 6)
        if rank in by_rank
    ]
    actual_best_predicted_rank = (
        max(replayed_pairs, key=lambda item: item[2])[0]
        if replayed_pairs
        else None
    )
    resource_factors = []
    for rank in trusted_ranks:
        if rank not in by_rank:
            continue
        candidate = candidates[rank - 1]
        record = by_rank[rank]
        predicted_resource = (
            record.get("predicted_resource")
            or candidate.get("predicted_resource")
            or {}
        )
        actual_resource = record.get("actual_resource") or {}
        for resource in RESOURCES:
            predicted = finite(
                predicted_resource.get(resource),
                f"rank{rank}.predicted_resource.{resource}",
            )
            actual = finite(
                actual_resource.get(resource),
                f"rank{rank}.actual_resource.{resource}",
            )
            factor = (
                max(actual / predicted, predicted / actual)
                if actual > 0.0 and predicted > 0.0
                else math.inf
            )
            resource_factors.append(
                {"rank": rank, "resource": resource, "factor_error": factor}
            )
    maximum_factor = max(
        (item["factor_error"] for item in resource_factors),
        default=None,
    )
    checks = {
        "candidate_audit_fields_complete": not missing_fields,
        "five_unique_three_level_fingerprints": (
            all(fingerprints) and len(set(fingerprints)) == 5
        ),
        "trusted3_probe2": roles == ["trusted", "trusted", "trusted", "probe", "probe"],
        "top5_no_technical_failure": len(failures) == 0 and len(by_rank) == 5,
        "trusted_only_spearman_positive": (
            spearman is not None and spearman > 0.0 and len(trusted_pairs) == 3
        ),
        "actual_best_in_predicted_top3": (
            actual_best_predicted_rank in {1, 2, 3}
        ),
        "trusted_resource_predictions_no_order_of_magnitude_error": (
            maximum_factor is not None
            and math.isfinite(maximum_factor)
            and maximum_factor < 10.0
            and len(resource_factors) == 9
        ),
    }
    return {
        "schema_version": "top5_validation_gate_analysis_v1",
        "physical_observation_count": len(observations),
        "valid_replay_count": len(by_rank),
        "technical_failure_count": len(failures),
        "roles": roles,
        "trusted_only_spearman": spearman,
        "trusted_score_mae": (
            sum(abs(error) for error in errors) / len(errors)
            if errors
            else None
        ),
        "trusted_score_bias_actual_minus_predicted": (
            sum(errors) / len(errors) if errors else None
        ),
        "actual_best_predicted_rank": actual_best_predicted_rank,
        "maximum_trusted_resource_factor_error": maximum_factor,
        "trusted_resource_factor_errors": resource_factors,
        "missing_candidate_fields": missing_fields,
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--event-name")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    selection = read_json(args.selection)
    if not isinstance(selection, Mapping):
        raise ValueError("selection must be a JSON object")
    result = analyze(
        selection,
        read_observations(args.observations, args.event_name),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
