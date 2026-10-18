#!/usr/bin/env python3
"""Build a deterministic JOB trusted3+probe2 repeat from one clean Top-5."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, Mapping


RESOURCES = ("cpu", "io", "memory")


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--minimum-score-span", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    selection = load(args.selection)
    candidates = selection.get("top5") or []
    events = [
        json.loads(line)
        for line in args.events.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    completed = {
        int(item["rank"]): item
        for item in events
        if item.get("event") == "job_round8_candidate_replay_complete"
    }
    technical = [
        item for item in events if item.get("event") == "job_round8_candidate_technical_failure"
    ]
    if len(candidates) != 5 or set(completed) != {1, 2, 3, 4, 5} or technical:
        raise RuntimeError("JOB source is not a clean completed Top-5")
    if any(item.get("technical_failure") is not False for item in completed.values()):
        raise RuntimeError("JOB source contains a technical failure")

    ranked = sorted(
        completed.values(),
        key=lambda item: (-float(item["actual_score"]), int(item["rank"])),
    )
    score_span = float(ranked[0]["actual_score"]) - float(ranked[-1]["actual_score"])
    if args.minimum_score_span < 0.0 or score_span <= args.minimum_score_span:
        raise RuntimeError(
            "JOB source score span does not exceed replay noise: "
            f"span={score_span:.12g} bound={args.minimum_score_span:.12g}"
        )
    ordered = [ranked[0], ranked[2], ranked[4], ranked[1], ranked[3]]
    source_by_rank = {int(item["rank"]): item for item in candidates}
    repeated = []
    events_sha = sha256(args.events)
    for rank, observation in enumerate(ordered, 1):
        source_rank = int(observation["rank"])
        candidate = copy.deepcopy(source_by_rank[source_rank])
        actual_score = float(observation["actual_score"])
        actual_resource = {
            name: float(observation["actual_resource"][name]) for name in RESOURCES
        }
        actual_utilization = {
            name: float(observation["actual_utilization"][name]) for name in RESOURCES
        }
        old_fingerprint = candidate["three_level_fingerprint"]
        fingerprint_payload = {
            "contract": "plan_feature_resource_three_level_v1",
            "plan_level": old_fingerprint["plan_level"],
            "feature_ratio_level": copy.deepcopy(old_fingerprint["feature_ratio_level"]),
            "predicted_resource_level": {
                name: int(round(actual_resource[name] * 1000.0)) for name in RESOURCES
            },
            "repeat_prediction_source": "first_clean_real_observation",
        }
        identifier = "three-level-sha256:" + canonical_sha(fingerprint_payload)
        trusted = rank <= 3
        candidate.update(
            {
                "rank": rank,
                "predicted_rank": rank,
                "source_rank": source_rank,
                "selection_role": "trusted" if trusted else "probe",
                "candidate_score": actual_score,
                "calibrated_candidate_score": actual_score,
                "selection_score": actual_score,
                "predicted_resource": actual_resource,
                "calibrated_predicted_resource": actual_resource,
                "final_resource": actual_resource,
                "utilization": actual_utilization,
                "confidence": {
                    "exact_plan_regime": 1.0,
                    "first_clean_real_observation": 1.0,
                    "independent_repeat_required": 1.0,
                },
                "used_for_gradient": trusted,
                "used_for_inner_incumbent_update": trusted,
                "incumbent_update_eligible": False,
                "three_level_fingerprint": {
                    **fingerprint_payload,
                    "id": identifier,
                    "parameter_fallback_used": False,
                    "valid": True,
                    "validation_errors": [],
                    "source_fingerprint_id": old_fingerprint["id"],
                },
                "source_provenance": {
                    "contract": "job_clean_formal_score_span_repeat_v1",
                    "source_rank": source_rank,
                    "source_events": str(args.events.resolve()),
                    "source_events_sha256": events_sha,
                },
            }
        )
        candidate["candidate_pool_binding"] = {
            **copy.deepcopy(candidate["candidate_pool_binding"]),
            "candidate_id": identifier,
        }
        candidate["diagnostic_snapshot"] = {
            field: copy.deepcopy(candidate[field])
            for field in (
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
        }
        repeated.append(candidate)

    identifiers = [item["three_level_fingerprint"]["id"] for item in repeated]
    if len(set(identifiers)) != 5:
        raise RuntimeError("JOB repeat fingerprints are not distinct")
    output = copy.deepcopy(selection)
    output.update(
        {
            "diagnostic_only": True,
            "diagnostic_role_evaluation_only": True,
            "formal_update_flags": {
                "accepted": False,
                "incumbent": False,
                "range": False,
                "gradient": False,
            },
            "trusted_count": 3,
            "probe_count": 2,
            "trusted_selection_strategy": (
                "deterministic_observed_score_span_best_median_worst_v1"
            ),
            "source_actual_score_span": score_span,
            "minimum_required_score_span": args.minimum_score_span,
            "score_span_gate_passed": True,
            "qps_used_for_inner_scoring": False,
            "top5": repeated,
            "ranked_candidates": repeated,
        }
    )
    atomic_json(args.output, output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "roles": [item["selection_role"] for item in repeated],
                "source_ranks": [item["source_rank"] for item in repeated],
                "score_span": score_span,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
