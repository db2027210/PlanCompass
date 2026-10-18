#!/usr/bin/env python3
"""JOB v64 formal runner executing only prequalified trusted candidates."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

import run_job_round8_strict_serial_robust_v11_gradient_state_guard as v11


v10 = v11.v10
base = v10.base
ELIGIBILITY_CONTRACT = "job_v64_robust_formal_eligibility_v1"


def eligibility_partition(
    selection_path: Path,
    state_path: Path,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], dict[str, Any]]:
    selection = base.load_json(selection_path.resolve())
    state = base.load_json(state_path.resolve())
    candidates = base.validate_selection(selection)
    roles = [str(item.get("selection_role") or "") for item in candidates]
    if roles != ["trusted", "trusted", "trusted", "probe", "probe"]:
        raise RuntimeError("v64 formal source is not trusted3+probe2")
    if (
        selection.get("robust_formal_eligibility_contract")
        != ELIGIBILITY_CONTRACT
        or selection.get("qps_used_for_inner_scoring") is not False
        or float(selection.get("robust_formal_incumbent_score"))
        != float(state.get("incumbent_score"))
        or selection.get("robust_formal_state_sha256")
        != hashlib.sha256(state_path.read_bytes()).hexdigest()
    ):
        raise RuntimeError("v64 formal eligibility authority mismatch")
    eligible = []
    skipped = []
    observed_ranks = []
    running_upper_bound = float(state["incumbent_score"])
    for rank, candidate in enumerate(candidates, 1):
        gate = candidate.get("robust_formal_eligibility")
        if not isinstance(gate, Mapping):
            raise RuntimeError(f"v64 rank {rank} eligibility gate is missing")
        gate_eligible = gate.get("eligible") is True
        role = str(candidate.get("selection_role") or "")
        if role == "trusted":
            prior_scores = [
                float(value) for value in v10.robust_v7.validate_policy(candidate)
            ]
            maximum = max(prior_scores)
            algorithmic_gate = candidate.get("algorithmic_gate")
            gate_allows = not (
                isinstance(algorithmic_gate, Mapping)
                and algorithmic_gate.get("accepted") is False
            )
            mathematically_eligible = bool(
                maximum > running_upper_bound + 1e-12 and gate_allows
            )
            expected_reason = (
                "eligible_trusted"
                if mathematically_eligible
                else (
                    "algorithmic_gate_rejected"
                    if not gate_allows
                    else "median_improvement_impossible"
                )
            )
        else:
            prior_scores = []
            maximum = None
            mathematically_eligible = False
            expected_reason = "probe_role"
        if (
            gate.get("contract") != ELIGIBILITY_CONTRACT
            or int(gate.get("source_rank", -1)) != rank
            or gate.get("selection_role") != candidate.get("selection_role")
            or gate.get("qps_used_for_inner_scoring") is not False
            or candidate.get("formal_replay_eligible") is not gate_eligible
            or gate_eligible is not mathematically_eligible
            or gate.get("reason") != expected_reason
            or gate.get("prior_actual_scores") != prior_scores
            or not math.isclose(
                float(gate.get("incumbent_threshold_before_rank")),
                running_upper_bound,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or (
                maximum is not None
                and not math.isclose(
                    float(gate.get("maximum_attainable_three_score_median")),
                    maximum,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            )
        ):
            raise RuntimeError(f"v64 rank {rank} eligibility binding mismatch")
        if gate_eligible:
            if (
                candidate.get("selection_role") != "trusted"
                or candidate.get("incumbent_update_eligible") is not True
                or candidate.get("used_for_inner_incumbent_update") is not True
            ):
                raise RuntimeError(f"v64 rank {rank} is illegally eligible")
            eligible.append(candidate)
            observed_ranks.append(rank)
            running_upper_bound = float(maximum)
        else:
            if candidate.get("selection_role") == "probe" and gate.get("reason") != "probe_role":
                raise RuntimeError(f"v64 probe rank {rank} skip reason mismatch")
            skipped.append(candidate)
    declared = [int(value) for value in selection.get("formal_replay_eligible_ranks") or []]
    if declared != observed_ranks or not eligible:
        raise RuntimeError("v64 eligible-rank ledger is empty or inconsistent")
    return eligible, skipped, selection


def run(args: argparse.Namespace) -> dict[str, Any]:
    selection_path = Path(args.selection).resolve()
    state_path = Path(args.state).resolve()
    eligible, skipped, _selection = eligibility_partition(selection_path, state_path)
    # Validate the original trusted3+probe2 and every sealed prior before
    # changing the base runner's in-process view to the eligible trusted set.
    v10.robust_v7._validate_formal_selection(selection_path)
    original_validate = base.validate_selection
    original_formal_validate = v10.robust_v7._validate_formal_selection

    def filtered_validate(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        original = original_validate(value)
        return [item for item in original if item.get("formal_replay_eligible") is True]

    base.validate_selection = filtered_validate
    v10.robust_v7._validate_formal_selection = lambda _path: None
    try:
        result = dict(v11.run(args))
    finally:
        base.validate_selection = original_validate
        v10.robust_v7._validate_formal_selection = original_formal_validate

    result["robust_formal_eligibility_contract"] = ELIGIBILITY_CONTRACT
    result["eligible_source_ranks"] = [
        int(item["robust_formal_eligibility"]["source_rank"])
        for item in eligible
    ]
    result["zero_call_skipped_source_ranks"] = [
        int(item["robust_formal_eligibility"]["source_rank"])
        for item in skipped
    ]
    result["zero_call_skip_count"] = len(skipped)
    result["qps_used_for_inner_scoring"] = False
    if int(result.get("physical_invocation_count", -1)) > len(eligible):
        raise RuntimeError("v64 formal runner exceeded eligible-call bound")
    summary_path = Path(str(result["summary_path"])).resolve()
    summary = dict(base.load_json(summary_path))
    summary.update(
        {
            "robust_formal_eligibility_contract": ELIGIBILITY_CONTRACT,
            "eligible_source_ranks": result["eligible_source_ranks"],
            "zero_call_skipped_source_ranks": result[
                "zero_call_skipped_source_ranks"
            ],
            "zero_call_skip_count": len(skipped),
            "qps_used_for_inner_scoring": False,
        }
    )
    temporary = summary_path.with_name(f".{summary_path.name}.v64.{os.getpid()}")
    temporary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, summary_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("replay_root", type=Path)
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--exclusion", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--accepted-target", type=int, default=10)
    parser.add_argument("--diagnostic-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.diagnostic_only:
        raise RuntimeError("v64 eligibility runner is formal-only")
    eligible, skipped, _selection = eligibility_partition(
        args.selection.resolve(), args.state.resolve()
    )
    if args.dry_run:
        v10.robust_v7._validate_formal_selection(args.selection.resolve())
        result = dict(
            base.preflight(
                args.selection.resolve(),
                args.authority_root.resolve(),
                args.observations.resolve(),
                args.exclusion.resolve(),
            )
        )
        result.update(
            {
                "candidate_count": 5,
                "eligible_formal_call_count": len(eligible),
                "zero_call_skip_count": len(skipped),
                "robust_formal_eligibility_contract": ELIGIBILITY_CONTRACT,
            }
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
