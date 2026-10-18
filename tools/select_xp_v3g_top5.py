#!/usr/bin/env python3
"""Select three calibrated XP regimes and two maximin uncertain probes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List


REQUIRED_AUDIT_FIELDS = (
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
TRUSTED_ANCHORS = (4, 1, 5)


def fingerprint_id(candidate: Dict[str, Any]) -> str:
    value = (candidate.get("three_level_fingerprint") or {}).get("id")
    if not value:
        raise ValueError("candidate lacks three-level fingerprint id")
    return str(value)


def anchor_rank(candidate: Dict[str, Any]) -> int:
    calibration = (
        (candidate.get("final_estimation_metadata") or {})
        .get("plan_signature_calibration") or {}
    )
    matches = calibration.get("matches") or {}
    anchors = {
        tuple((matches.get(resource) or {}).get("anchor_ranks") or [])
        for resource in ("cpu", "io", "memory")
    }
    if len(anchors) != 1:
        raise ValueError(f"inconsistent calibrated anchors for {fingerprint_id(candidate)}")
    values = next(iter(anchors))
    if len(values) != 1:
        raise ValueError(f"candidate has no exact single anchor: {fingerprint_id(candidate)}")
    return int(values[0])


def validate_candidate(candidate: Dict[str, Any]) -> None:
    fingerprint_id(candidate)
    if candidate.get("non_corner") is not True:
        raise ValueError("corner candidate is forbidden")
    if candidate.get("novel_against_authoritative_round9") is not True:
        raise ValueError("candidate is not novel")
    if candidate.get("workload_executed") is not False:
        raise ValueError("generation candidate unexpectedly executed workload")
    for field in REQUIRED_AUDIT_FIELDS:
        if field not in candidate or candidate.get(field) is None:
            raise ValueError(f"candidate missing audit field {field}")


def normalized_vectors(candidates: List[Dict[str, Any]]) -> Dict[str, List[float]]:
    param_names = sorted({
        name
        for candidate in candidates
        for name in (candidate.get("candidate_params") or {})
    })
    raw: Dict[str, List[float]] = {}
    for candidate in candidates:
        params = candidate.get("candidate_params") or {}
        feature = candidate.get("feature_ratio") or {}
        predicted = candidate.get("predicted_resource") or {}
        vector = [
            float(feature.get(resource, 0.0))
            for resource in ("cpu", "io", "memory")
        ]
        vector.extend(
            float(predicted.get(resource, 0.0))
            for resource in ("cpu", "io", "memory")
        )
        vector.extend(math.log(max(float(params.get(name, 0.0)), 1e-12)) for name in param_names)
        raw[fingerprint_id(candidate)] = vector
    columns = list(zip(*raw.values()))
    lows = [min(column) for column in columns]
    highs = [max(column) for column in columns]
    return {
        identifier: [
            0.0 if high == low else (value - low) / (high - low)
            for value, low, high in zip(vector, lows, highs)
        ]
        for identifier, vector in raw.items()
    }


def distance(left: List[float], right: List[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def select(source: Dict[str, Any]) -> Dict[str, Any]:
    candidates = [copy.deepcopy(item) for item in source.get("candidate_rankings") or []]
    if len(candidates) < 5:
        raise ValueError("fewer than five candidates")
    for candidate in candidates:
        validate_candidate(candidate)
    identifiers = [fingerprint_id(candidate) for candidate in candidates]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate three-level fingerprint")

    trusted_pool = [
        candidate
        for candidate in candidates
        if candidate.get("selection_role_eligibility") == "trusted"
        and candidate.get("calibrated_plan_regime") is True
    ]
    trusted: List[Dict[str, Any]] = []
    for expected_anchor in TRUSTED_ANCHORS:
        matches = [candidate for candidate in trusted_pool if anchor_rank(candidate) == expected_anchor]
        if not matches:
            raise ValueError(f"missing trusted candidate for anchor {expected_anchor}")
        trusted.append(max(matches, key=lambda item: float(item["candidate_score"])))

    probes = [
        candidate
        for candidate in candidates
        if candidate.get("selection_role_eligibility") == "probe_only"
        and candidate.get("calibrated_plan_regime") is False
    ]
    if len(probes) < 2:
        raise ValueError("fewer than two uncertain probes")
    vectors = normalized_vectors(candidates)
    chosen: List[Dict[str, Any]] = list(trusted)
    selected_probes: List[Dict[str, Any]] = []
    while len(selected_probes) < 2:
        remaining = [candidate for candidate in probes if candidate not in selected_probes]
        picked = max(
            remaining,
            key=lambda candidate: (
                min(
                    distance(
                        vectors[fingerprint_id(candidate)],
                        vectors[fingerprint_id(existing)],
                    )
                    for existing in chosen
                ),
                float(candidate["candidate_score"]),
                fingerprint_id(candidate),
            ),
        )
        selected_probes.append(picked)
        chosen.append(picked)

    top5: List[Dict[str, Any]] = []
    for rank, candidate in enumerate(trusted + selected_probes, 1):
        item = copy.deepcopy(candidate)
        item["rank"] = rank
        item["predicted_rank"] = rank
        item["selection_role"] = "trusted" if rank <= 3 else "uncertain_probe"
        item["diagnostic_only"] = True
        item["accepted_update_applied"] = False
        item["gradient_update_applied"] = False
        item["incumbent_update_applied"] = False
        item["range_update_applied"] = False
        if rank <= 3:
            item["validated_anchor_rank"] = anchor_rank(item)
        top5.append(item)

    return {
        "contract": "xp_v3g_trusted3_probe2_diagnostic_top5_v1",
        "source_status": source.get("status"),
        "source_novel_non_corner_fingerprint_count": source.get(
            "novel_non_corner_fingerprint_count"
        ),
        "source_trusted_eligible_count": source.get("trusted_eligible_count"),
        "top5": top5,
        "trusted_count": 3,
        "uncertain_probe_count": 2,
        "maximum_physical_workload_calls": 5,
        "serial_real_replay": True,
        "diagnostic_only": True,
        "accepted_shrink_target": 10,
        "qps_used_for_inner_scoring": False,
        "qps_role": "outer_audit_only",
        "technical_failure_consumes_physical_call_cap": True,
        "technical_failure_counts_as_valid_replay": False,
        "rejected_updates_state": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source_bytes = args.source.read_bytes()
    source = json.loads(source_bytes)
    payload = select(source)
    payload["source_path"] = str(args.source)
    payload["source_sha256"] = hashlib.sha256(source_bytes).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "top5": [
            {
                "rank": item["rank"],
                "role": item["selection_role"],
                "fingerprint": fingerprint_id(item),
                "predicted_score": item["candidate_score"],
            }
            for item in payload["top5"]
        ],
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
