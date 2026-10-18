#!/usr/bin/env python3
"""Evaluate the TPC-C formal-entry gate from the robust diagnostic Top-5."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List


ROUND = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "gradient_debug_round14_robust_regime_interval_v2_20260729T1824"
)
DISCOVERY_ROUND = ROUND.parent / "gradient_debug_round12_current_plan_discovery_v1_20260729"
SELECTION = ROUND / "top5.json"
LEDGER = ROUND / "strict_top5_real_replay_round14_ledger_v1.json"
SUMMARY = ROUND / "top5_real_replay_round14_robust_trusted3_probe2/summary.json"
DISCOVERY_SELECTION = DISCOVERY_ROUND / "top5.json"
DISCOVERY_SUMMARY = (
    DISCOVERY_ROUND / "top5_real_replay_round12_probe5_v1/summary.json"
)
OUTPUT = ROUND / "formal_entry_gate.json"
EXPECTED_SELECTION_SHA = (
    "a973acb0608118be851d21625dc781b1ff4bd4e0cdb26ea8b68e44fb2f798d8b"
)
EXPECTED_DISCOVERY_SELECTION_SHA = (
    "4de31b3e0c4910725c8ebf10a5f07972b0a907af11a6901ffcf30ada79c9c093"
)
RESOURCES = ("cpu", "memory", "io")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def average_ranks(values: List[float], *, descending: bool) -> List[float]:
    order = sorted(
        range(len(values)), key=lambda index: values[index], reverse=descending
    )
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and math.isclose(
            values[order[end]], values[order[cursor]], rel_tol=0.0, abs_tol=1e-12
        ):
            end += 1
        rank = ((cursor + 1) + end) / 2.0
        for position in range(cursor, end):
            ranks[order[position]] = rank
        cursor = end
    return ranks


def pearson(left: List[float], right: List[float]) -> float:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (a - left_mean) * (b - right_mean) for a, b in zip(left, right)
    )
    left_norm = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_norm = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        raise RuntimeError("trusted Spearman is undefined")
    return numerator / (left_norm * right_norm)


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    if sha256(SELECTION) != EXPECTED_SELECTION_SHA:
        raise RuntimeError("round14 selection SHA mismatch")
    if sha256(DISCOVERY_SELECTION) != EXPECTED_DISCOVERY_SELECTION_SHA:
        raise RuntimeError("round12 discovery selection SHA mismatch")
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    discovery = json.loads(DISCOVERY_SELECTION.read_text(encoding="utf-8"))
    discovery_summary = json.loads(DISCOVERY_SUMMARY.read_text(encoding="utf-8"))

    roles = [item["selection_role"] for item in selection["top5"]]
    roles_passed = roles == [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]
    clean_top5 = bool(
        ledger.get("real_workload_invocation_count") == 5
        and len(ledger.get("successful", [])) == 5
        and ledger.get("technical_failures") == []
        and summary.get("full_top5_complete") is True
        and summary.get("technical_failure_count") == 0
    )
    baseline_restored = bool(
        summary.get("baseline_params_restored") is True
        and summary.get("baseline_snapshot_restored") is True
        and summary.get("accepted_count_delta") == 0
        and summary.get("incumbent_updated") is False
        and summary.get("range_updated") is False
        and summary.get("gradient_update_applied") is False
    )
    discovery_ids = [
        item["three_level_fingerprint"]["id"] for item in discovery["top5"]
    ]
    discovery_diversity = bool(
        len(discovery_ids) == 5
        and len(set(discovery_ids)) == 5
        and all(
            item.get("boundary_corner") is False
            and item.get("boundary_plateau") is False
            for item in discovery["top5"]
        )
        and discovery_summary.get("full_top5_complete") is True
        and discovery_summary.get("technical_failure_count") == 0
        and discovery_summary.get("baseline_params_restored") is True
        and discovery_summary.get("baseline_snapshot_restored") is True
        and discovery_summary.get("accepted_count_delta") == 0
    )

    results = {int(item["rank"]): item for item in ledger.get("successful", [])}
    candidates = {int(item["rank"]): item for item in selection["top5"]}
    trusted_ranks = [1, 2, 3]
    predicted_scores = [
        float(candidates[rank]["candidate_score"]) for rank in trusted_ranks
    ]
    actual_scores = [float(results[rank]["actual_score"]) for rank in trusted_ranks]
    spearman = pearson(
        average_ranks(predicted_scores, descending=True),
        average_ranks(actual_scores, descending=True),
    )
    all_actual_scores = {
        rank: float(result["actual_score"]) for rank, result in results.items()
    }
    best_score = max(all_actual_scores.values())
    actual_best_ranks = sorted(
        rank
        for rank, value in all_actual_scores.items()
        if math.isclose(value, best_score, rel_tol=0.0, abs_tol=1e-12)
    )
    predicted_top3_ranks = sorted(
        candidates,
        key=lambda rank: (
            -float(candidates[rank]["candidate_score"]),
            rank,
        ),
    )[:3]
    actual_best_in_predicted_top3 = bool(
        set(actual_best_ranks) & set(predicted_top3_ranks)
    )

    resource_ratios: Dict[str, Dict[str, float]] = {}
    resource_magnitude_passed = True
    for rank in trusted_ranks:
        resource_ratios[str(rank)] = {}
        for name in RESOURCES:
            predicted = float(candidates[rank]["predicted_resource"][name])
            actual = float(results[rank]["actual_resource"][name])
            ratio = actual / predicted
            resource_ratios[str(rank)][name] = ratio
            if not (0.1 <= ratio <= 10.0):
                resource_magnitude_passed = False

    qps_outer_only = bool(
        selection.get("qps_role") == "outer_audit_only_not_inner_score"
        and all(
            item.get("qps_used_for_inner_scoring") is False
            for item in selection["top5"]
        )
    )
    passed = all(
        (
            discovery_diversity,
            roles_passed,
            clean_top5,
            spearman > 0.0,
            actual_best_in_predicted_top3,
            resource_magnitude_passed,
            baseline_restored,
            qps_outer_only,
        )
    )
    payload = {
        "schema_version": "tpcc_formal_entry_gate_v1",
        "passed": passed,
        "selection_path": str(SELECTION),
        "selection_sha256": EXPECTED_SELECTION_SHA,
        "ledger_path": str(LEDGER),
        "ledger_sha256": sha256(LEDGER),
        "summary_path": str(SUMMARY),
        "summary_sha256": sha256(SUMMARY),
        "checks": {
            "five_novel_noncorner_discovery_fingerprints": discovery_diversity,
            "top5_roles_trusted3_probe2": roles_passed,
            "top5_clean_5_of_5": clean_top5,
            "trusted_only_spearman_positive": spearman > 0.0,
            "actual_best_in_predicted_top3": actual_best_in_predicted_top3,
            "trusted_resource_prediction_no_order_magnitude_error": (
                resource_magnitude_passed
            ),
            "baseline_restored_and_no_diagnostic_state_pollution": (
                baseline_restored
            ),
            "qps_outer_only": qps_outer_only,
        },
        "trusted_only_spearman": spearman,
        "trusted_predicted_scores": predicted_scores,
        "trusted_actual_scores": actual_scores,
        "actual_best_ranks": actual_best_ranks,
        "predicted_top3_ranks_by_candidate_score": predicted_top3_ranks,
        "trusted_resource_actual_over_predicted": resource_ratios,
        "accepted_count_delta": 0,
        "formal_start_authorized": passed,
    }
    atomic_json(OUTPUT, payload)
    print(json.dumps(payload, sort_keys=True), flush=True)
    if not passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
