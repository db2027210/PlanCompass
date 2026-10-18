#!/usr/bin/env python3
"""Evaluate the fixed JOB clean5 formal-entry gate without state mutation."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any, Dict


RESOURCES = ("cpu", "io", "memory")


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    result = [0.0] * len(values)
    for rank, index in enumerate(order, 1):
        result[index] = float(rank)
    return result


def spearman(left: list[float], right: list[float]) -> float:
    left_ranks = ranks(left)
    right_ranks = ranks(right)
    left_mean = sum(left_ranks) / len(left_ranks)
    right_mean = sum(right_ranks) / len(right_ranks)
    numerator = sum(
        (x - left_mean) * (y - right_mean)
        for x, y in zip(left_ranks, right_ranks)
    )
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left_ranks)
        * sum((y - right_mean) ** 2 for y in right_ranks)
    )
    if denominator == 0.0:
        raise RuntimeError("trusted ranks are not distinguishable")
    return numerator / denominator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    selection = load(args.selection)
    summary = load(args.summary)
    candidates = selection.get("top5") or []
    successful = summary.get("successful") or []
    observations = {int(item["rank"]): item for item in successful}
    if len(candidates) != 5 or set(observations) != {1, 2, 3, 4, 5}:
        raise RuntimeError("JOB gate requires one complete Top-5")
    trusted = candidates[:3]
    predicted = [float(item["candidate_score"]) for item in trusted]
    actual = [float(observations[rank]["actual_score"]) for rank in (1, 2, 3)]
    all_predicted = [
        float(candidate["candidate_score"]) for candidate in candidates
    ]
    all_actual = [
        float(observations[rank]["actual_score"])
        for rank in range(1, 6)
    ]
    predicted_trusted_span = max(predicted) - min(predicted)
    # Unknown-regime probes measure acquisition/calibration bias, not replay
    # noise.  Mixing their prediction misses into the noise floor can make a
    # single useful probe hide an otherwise rankable trusted regime.  Estimate
    # repeat noise only from the three exact-plan trusted observations; probes
    # remain fully visible in the outer audit below.
    trusted_replay_noise_two_mae = 2.0 * sum(
        abs(before - after)
        for before, after in zip(predicted, actual)
    ) / len(predicted)
    rho = spearman(predicted, actual)
    factors = {
        rank: {
            resource: max(
                float(candidate["predicted_resource"][resource])
                / float(observations[rank]["actual_resource"][resource]),
                float(observations[rank]["actual_resource"][resource])
                / float(candidate["predicted_resource"][resource]),
            )
            for resource in RESOURCES
        }
        for rank, candidate in enumerate(trusted, 1)
    }
    maximum_factor = max(
        value for row in factors.values() for value in row.values()
    )
    actual_best = max(
        observations,
        key=lambda rank: float(observations[rank]["actual_score"]),
    )
    fingerprints = [
        item["three_level_fingerprint"]["id"] for item in candidates
    ]
    checks = {
        "five_novel_noncorner_fingerprints": (
            len(set(fingerprints)) == 5
            and all(item.get("boundary_corner") is False for item in candidates)
        ),
        "trusted3_probe2": [item["selection_role"] for item in candidates]
        == ["trusted", "trusted", "trusted", "probe", "probe"],
        "top5_clean_no_technical_failure": (
            summary.get("physical_invocation_count") == 5
            and summary.get("successful_real_replay_count") == 5
            and summary.get("technical_failure_count") == 0
        ),
        "trusted_only_spearman_positive": rho > 0.0,
        "actual_best_in_predicted_top3": actual_best <= 3,
        "trusted_resource_no_order_of_magnitude_error": maximum_factor < 10.0,
        "baseline_restored": summary.get("baseline_params_restored") is True,
        "diagnostic_state_neutral": (
            summary.get("diagnostic_only") is True
            and summary.get("initial_state") == summary.get("final_state")
            and summary.get("formal_update_flags")
            == {
                "accepted": False,
                "gradient": False,
                "incumbent": False,
                "range": False,
            }
        ),
    }
    trusted_score_span_distinguishable = (
        predicted_trusted_span > trusted_replay_noise_two_mae
    )
    output = {
        "schema_version": "job_clean5_formal_entry_gate_v1",
        "passed": all(checks.values()),
        "checks": checks,
        "formal_accepted_total": 0,
        "accepted_target": 10,
        "trusted_only_spearman": rho,
        "actual_best_predicted_rank": actual_best,
        "maximum_trusted_resource_factor": maximum_factor,
        "trusted_resource_factors": factors,
        "predicted_trusted_scores": predicted,
        "actual_trusted_scores": actual,
        "all_predicted_scores": all_predicted,
        "all_actual_scores": all_actual,
        "predicted_trusted_score_span": predicted_trusted_span,
        "trusted_replay_noise_two_mae": trusted_replay_noise_two_mae,
        "trusted_score_span_distinguishable": (
            trusted_score_span_distinguishable
        ),
        "gradient_rank_update_allowed": trusted_score_span_distinguishable,
        "score_span_noise_contract": (
            "trusted_predicted_span_vs_trusted_exact_repeat_two_mae_audit_v2"
        ),
    }
    rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        temporary = args.output.with_name(
            f".{args.output.name}.tmp.{os.getpid()}"
        )
        temporary.write_text(rendered, encoding="utf-8")
        os.replace(temporary, args.output)
    print(rendered, end="", flush=True)
    return 0 if output["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
