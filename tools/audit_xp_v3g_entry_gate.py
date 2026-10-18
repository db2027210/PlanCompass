#!/usr/bin/env python3
"""Audit the XP v3g Top-5 against the formal-entry contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List


RESOURCES = ("cpu", "io", "memory")


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ranks_desc(values: List[float]) -> List[int]:
    order = sorted(range(len(values)), key=lambda index: (-values[index], index))
    result = [0] * len(values)
    for rank, index in enumerate(order, 1):
        result[index] = rank
    return result


def spearman(left: List[float], right: List[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("Spearman inputs are invalid")
    left_rank = ranks_desc(left)
    right_rank = ranks_desc(right)
    count = len(left)
    return 1.0 - 6.0 * sum(
        (a - b) ** 2 for a, b in zip(left_rank, right_rank)
    ) / (count * (count * count - 1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--accepted-incumbent", required=True, type=Path)
    parser.add_argument("--accepted-incumbent-authority", required=True, type=Path)
    parser.add_argument("--counter", action="append", default=[], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    generation = load(args.generation)
    selection = load(args.selection)
    summary = load(args.summary)
    top5 = selection.get("top5") or []
    successful = summary.get("successful") or []
    by_rank = {int(item["rank"]): item for item in successful}
    trusted = [by_rank[rank] for rank in (1, 2, 3) if rank in by_rank]
    predicted = [float(item["predicted_score"]) for item in trusted]
    actual = [float(item["actual_score"]) for item in trusted]
    trusted_spearman = spearman(predicted, actual) if len(trusted) == 3 else None
    actual_best_rank = (
        max(successful, key=lambda item: float(item["actual_score"]))["rank"]
        if successful else None
    )
    resource_factors = []
    for item in trusted:
        factors = {}
        for resource in RESOURCES:
            predicted_value = float(item["predicted_resource"][resource])
            actual_value = float(item["actual_resource"][resource])
            factors[resource] = max(
                predicted_value / actual_value,
                actual_value / predicted_value,
            )
        resource_factors.append({"rank": item["rank"], "factor": factors})
    no_order_error = all(
        math.isfinite(factor) and factor < 10.0
        for item in resource_factors
        for factor in item["factor"].values()
    )
    counters = {
        str(path): path.read_text(encoding="utf-8").strip()
        for path in args.counter
    }
    accepted_hash = sha256(args.accepted_incumbent)
    authority_hash = sha256(args.accepted_incumbent_authority)
    checks = {
        "novel_non_corner_at_least_five": int(
            generation.get("novel_non_corner_fingerprint_count", 0)
        ) >= 5,
        "trusted3_probe2": [
            item.get("selection_role") for item in top5
        ] == ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"],
        "five_physical_calls": summary.get("physical_workload_call_count") == 5,
        "five_valid_replays": summary.get("valid_replay_count") == 5,
        "zero_technical_failures": summary.get("technical_failure_count") == 0,
        "trusted_spearman_positive": (
            trusted_spearman is not None and trusted_spearman > 0.0
        ),
        "actual_best_in_predicted_top3": (
            actual_best_rank is not None and int(actual_best_rank) <= 3
        ),
        "trusted_resource_no_order_error": no_order_error,
        "baseline_restored": summary.get("baseline_restored_after_every_point") is True,
        "diagnostic_only": summary.get("diagnostic_only") is True,
        "accepted_delta_zero": summary.get("accepted_count_delta") == 0,
        "incumbent_unchanged": summary.get("incumbent_updated") is False,
        "range_unchanged": summary.get("range_updated") is False,
        "gradient_unchanged": summary.get("gradient_updated") is False,
        "accepted_incumbent_authority_unchanged": accepted_hash == authority_hash,
        "formal_counters_unchanged_zero": all(value == "0" for value in counters.values()),
        "qps_outer_only": summary.get("qps_used_for_inner_scoring") is False,
    }
    result = {
        "contract": "xp_v3g_formal_entry_gate_audit_v1",
        "gate_passed": all(checks.values()),
        "checks": checks,
        "trusted_spearman": trusted_spearman,
        "actual_best_replay_rank": actual_best_rank,
        "trusted_resource_prediction_factors": resource_factors,
        "accepted_incumbent_sha256": accepted_hash,
        "accepted_incumbent_authority_sha256": authority_hash,
        "formal_counters": counters,
        "generation_sha256": sha256(args.generation),
        "selection_sha256": sha256(args.selection),
        "summary_sha256": sha256(args.summary),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0 if result["gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
