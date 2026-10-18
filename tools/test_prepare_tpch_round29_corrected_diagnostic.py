#!/usr/bin/env python3
"""Offline self-test for the TPC-H round29 diagnostic package builder."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import tempfile
from typing import Any, Dict, Mapping

import prepare_tpch_round29_corrected_diagnostic as builder
import tpch_round19_plan_regime_select as selector


REAL_SELECTION = Path("/tmp/tpch_round28_output/top5.json")
REAL_EXPANSION = Path("/tmp/tpch_round28_input/expanded_candidates.json")


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def unique_clone(
    candidate: Mapping[str, Any],
    *,
    suffix: int,
) -> Dict[str, Any]:
    clone = copy.deepcopy(dict(candidate))
    clone["candidate_params"]["random_page_cost"] = (
        float(clone["candidate_params"]["random_page_cost"]) + suffix * 0.001
    )
    clone["optimized_params"] = copy.deepcopy(clone["candidate_params"])
    fingerprint = copy.deepcopy(clone["three_level_fingerprint"])
    fingerprint["id"] = f"three-level-sha256:{suffix:064x}"
    clone["three_level_fingerprint"] = fingerprint
    clone.pop("structural_plan_regime_id", None)
    return clone


def run_self_test() -> Dict[str, Any]:
    if not REAL_SELECTION.is_file() or not REAL_EXPANSION.is_file():
        raise RuntimeError("round28 local fixtures are missing")
    selection = builder.load(REAL_SELECTION)
    source_by_rank = builder.validate_round28_selection(selection)
    real_expansion = builder.load(REAL_EXPANSION)["ranked_candidates"]

    results = []
    for rank in range(1, 6):
        source = source_by_rank[rank]
        row = copy.deepcopy(dict(source))
        row.update(
            {
                "rank": rank,
                "selection_role": source["selection_role"],
                "technical_failure": False,
                "valid_replay": True,
                "actual_resource": {
                    name: float(source["predicted_resource"][name])
                    * (1.0 + rank * 0.01)
                    for name in builder.RESOURCES
                },
                "actual_score": builder.EXPECTED_ACTUAL_SCORES[rank - 1],
            }
        )
        results.append(row)

    trusted_clones = [
        unique_clone(source_by_rank[rank], suffix=100 + rank)
        for rank in (1, 2, 3)
    ]
    known_regimes = set(selection["validated_plan_regimes"])
    unknown = [
        candidate
        for candidate in real_expansion
        if selector.structural_plan_regime_id(candidate) not in known_regimes
        and candidate.get("boundary_corner") is not True
        and candidate.get("boundary_plateau") is not True
    ][:2]
    if len(unknown) != 2:
        raise RuntimeError("real expansion lacks two unknown-regime test candidates")
    unknown_clones = [
        unique_clone(candidate, suffix=200 + index)
        for index, candidate in enumerate(unknown, 1)
    ]

    with tempfile.TemporaryDirectory(prefix="tpch_round29_test_") as name:
        root = Path(name)
        results_path = root / "results.json"
        state_path = root / "state.json"
        expansion_path = root / "expansion.json"
        output_path = root / "output"
        write_json(
            results_path,
            {
                "physical_call_count": 5,
                "valid_replay_count": 5,
                "technical_failure_count": 0,
                "results": results,
            },
        )
        write_json(
            state_path,
            {
                "baseline_restored": True,
                "technical_failure_count": 0,
                "accepted_count": builder.EXPECTED_ACCEPTED_COUNT,
                "incumbent_score": builder.EXPECTED_INCUMBENT_SCORE,
            },
        )
        write_json(
            expansion_path,
            {"ranked_candidates": [*trusted_clones, *unknown_clones]},
        )
        args = argparse.Namespace(
            round28_selection=str(REAL_SELECTION),
            round28_results=str(results_path),
            round28_state=str(state_path),
            expanded_candidates=str(expansion_path),
            output=str(output_path),
        )
        builder.build(args)
        package = builder.load(output_path / "top5.json")
        roles = [candidate["selection_role"] for candidate in package["top5"]]
        if roles != ["trusted", "trusted", "trusted", "probe", "probe"]:
            raise RuntimeError(f"unexpected roles: {roles}")
        for candidate in package["top5"]:
            for field in (
                "accepted_update_allowed",
                "incumbent_update_allowed",
                "range_update_allowed",
                "gradient_update_allowed",
            ):
                if candidate[field] is not False:
                    raise RuntimeError(f"rank {candidate['rank']} allows {field}")
        probe_feedback = [
            row
            for row in package["round28_feedback_audit"]
            if row["selection_role"] == "probe"
        ]
        if len(probe_feedback) != 2 or any(
            row["used_for_exact_regime_calibration"] for row in probe_feedback
        ):
            raise RuntimeError("probe feedback contaminated calibration")
        return {
            "status": "passed",
            "roles": roles,
            "probe_calibration_updates": 0,
            "diagnostic_state_updates_allowed": 0,
            "selected_unique_fingerprint_count": package["selection_audit"][
                "selected_unique_fingerprint_count"
            ],
        }


def main() -> int:
    print(json.dumps(run_self_test(), indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
