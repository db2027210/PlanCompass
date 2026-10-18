#!/usr/bin/env python3
"""Reconstruct JOB formal acceptance from three exact-point observations."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping


PARAMETER_NAMES = (
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "cpu_tuple_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
    "random_page_cost",
    "seq_page_cost",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} must contain one JSON object")
    return value


def load_events(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError(f"{path}:{line_number} is not an object")
        events.append(value)
    return events


def finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError(f"{label} must be finite")
    return result


def replay_events(events: Iterable[Mapping[str, Any]]) -> Dict[int, Mapping[str, Any]]:
    rows: Dict[int, Mapping[str, Any]] = {}
    for event in events:
        if event.get("event") != "job_round8_candidate_replay_complete":
            continue
        if event.get("technical_failure") is True:
            continue
        rank = event.get("rank")
        if type(rank) is not int or rank <= 0 or rank in rows:
            raise RuntimeError("JOB replay event ranks must be unique positive integers")
        rows[rank] = event
    return rows


def candidate_params(event: Mapping[str, Any]) -> Mapping[str, Any]:
    binding = event.get("candidate_binding")
    if not isinstance(binding, dict):
        raise RuntimeError("candidate_binding is missing from replay event")
    params = binding.get("candidate_params")
    if not isinstance(params, dict):
        raise RuntimeError("candidate_binding.candidate_params is missing")
    return params


def exact_params_match(
    expected: Mapping[str, Any], observed: Mapping[str, Any]
) -> bool:
    if set(expected) != set(PARAMETER_NAMES) or set(observed) != set(PARAMETER_NAMES):
        return False
    return all(
        math.isclose(
            finite(expected[name], f"expected {name}"),
            finite(observed[name], f"observed {name}"),
            rel_tol=5e-12,
            abs_tol=5e-15,
        )
        for name in PARAMETER_NAMES
    )


def audit(
    selection: Mapping[str, Any],
    first_events_path: Path,
    first_events: Iterable[Mapping[str, Any]],
    formal_events: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    top5 = selection.get("top5")
    if not isinstance(top5, list) or len(top5) != 5:
        raise RuntimeError("formal selection must contain Top-5")
    trusted = [item for item in top5 if item.get("selection_role") == "trusted"]
    if len(trusted) != 3:
        raise RuntimeError("formal selection must contain exactly three trusted points")

    first_sha = sha256_file(first_events_path)
    first_by_rank = replay_events(first_events)
    formal_by_rank = replay_events(formal_events)
    second_scores = selection.get("source_first_observation_actual_scores")
    if not isinstance(second_scores, dict):
        raise RuntimeError("sealed second-observation scores are missing")

    incumbent = finite(trusted[0].get("initial_score"), "initial incumbent score")
    accepted_count = 0
    rows: List[Dict[str, Any]] = []
    for candidate in sorted(trusted, key=lambda item: int(item["rank"])):
        rank = int(candidate["rank"])
        source_rank = int(candidate["source_rank"])
        expected_params = candidate.get("candidate_params")
        if not isinstance(expected_params, dict):
            raise RuntimeError(f"trusted rank {rank} candidate_params missing")

        provenance = candidate.get("source_provenance")
        if not isinstance(provenance, dict):
            raise RuntimeError(f"trusted rank {rank} source_provenance missing")
        if provenance.get("source_events_sha256") != first_sha:
            raise RuntimeError(f"trusted rank {rank} first-event SHA mismatch")
        if int(provenance.get("source_rank", -1)) != source_rank:
            raise RuntimeError(f"trusted rank {rank} source-rank provenance mismatch")

        first = first_by_rank.get(source_rank)
        third = formal_by_rank.get(rank)
        if first is None or third is None:
            raise RuntimeError(f"trusted rank {rank} is missing an observation")
        if not exact_params_match(expected_params, candidate_params(first)):
            raise RuntimeError(f"trusted rank {rank} first observation is not exact-point")
        if not exact_params_match(expected_params, candidate_params(third)):
            raise RuntimeError(f"trusted rank {rank} formal observation is not exact-point")

        scores = [
            finite(first.get("actual_score"), f"rank {rank} observation 1"),
            finite(second_scores.get(str(source_rank)), f"rank {rank} observation 2"),
            finite(third.get("actual_score"), f"rank {rank} observation 3"),
        ]
        robust_score = float(statistics.median(scores))
        incumbent_before = incumbent
        accepted = robust_score > incumbent_before + 1e-12
        if accepted:
            incumbent = robust_score
            accepted_count += 1
        rows.append(
            {
                "rank": rank,
                "source_rank": source_rank,
                "candidate_params": dict(expected_params),
                "independent_actual_scores": scores,
                "robust_score": robust_score,
                "incumbent_before": incumbent_before,
                "accepted": accepted,
                "incumbent_after": incumbent,
            }
        )

    return {
        "contract": "job_three_independent_exact_point_median_acceptance_v1",
        "selection_sha256": None,
        "first_events_sha256": first_sha,
        "trusted_rows": rows,
        "reconstructed_accepted_delta": accepted_count,
        "reconstructed_incumbent_score": incumbent,
        "qps_used_for_inner_scoring": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--first-events", type=Path, required=True)
    parser.add_argument("--formal-events", type=Path, required=True)
    args = parser.parse_args()

    result = audit(
        load_json(args.selection),
        args.first_events,
        load_events(args.first_events),
        load_events(args.formal_events),
    )
    result["selection_sha256"] = sha256_file(args.selection)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
