#!/usr/bin/env python3
"""JOB v65 formal runner with real nested filtering and dynamic accounting."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

import run_job_round8_strict_serial_robust_v11_gradient_state_guard as v11


v10 = v11.v10
base = v10.base
frozen = base.v10.frozen
ELIGIBILITY_CONTRACT = "job_v65_resource_median_reachability_v1"
RUNNER_CONTRACT = "job_v65_nested_frozen_filter_dynamic_count_v1"


def eligibility_partition(
    selection_path: Path,
    state_path: Path,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], dict[str, Any]]:
    selection = base.load_json(selection_path.resolve())
    state = base.load_json(state_path.resolve())
    candidates = base.validate_selection(selection)
    if (
        [str(item.get("selection_role") or "") for item in candidates]
        != ["trusted", "trusted", "trusted", "probe", "probe"]
        or selection.get("robust_formal_eligibility_contract")
        != ELIGIBILITY_CONTRACT
        or selection.get("qps_used_for_inner_scoring") is not False
        or float(selection.get("robust_formal_incumbent_score"))
        != float(state.get("incumbent_score"))
        or selection.get("robust_formal_state_sha256")
        != hashlib.sha256(state_path.read_bytes()).hexdigest()
    ):
        raise RuntimeError("v65 formal eligibility authority mismatch")
    eligible = []
    skipped = []
    observed_ranks = []
    for rank, candidate in enumerate(candidates, 1):
        gate = candidate.get("robust_formal_eligibility")
        if (
            not isinstance(gate, Mapping)
            or gate.get("contract") != ELIGIBILITY_CONTRACT
            or int(gate.get("source_rank", -1)) != rank
            or gate.get("selection_role") != candidate.get("selection_role")
            or gate.get("qps_used_for_inner_scoring") is not False
            or candidate.get("formal_replay_eligible")
            is not (gate.get("eligible") is True)
        ):
            raise RuntimeError(f"v65 rank {rank} eligibility binding mismatch")
        if gate.get("eligible") is True:
            if (
                candidate.get("selection_role") != "trusted"
                or candidate.get("incumbent_update_eligible") is not True
                or candidate.get("used_for_inner_incumbent_update") is not True
                or candidate.get("used_for_gradient") is not True
            ):
                raise RuntimeError(f"v65 rank {rank} is illegally eligible")
            # v10 verifies exact plan, two independent SHAs, parameters,
            # resources and utilization before any workload invocation.
            v10._prior_evidence(candidate)
            eligible.append(candidate)
            observed_ranks.append(rank)
        else:
            if candidate.get("selection_role") == "probe":
                if gate.get("reason") != "probe_role":
                    raise RuntimeError(f"v65 probe rank {rank} skip reason mismatch")
            elif gate.get("reason") not in (
                "resource_median_improvement_impossible",
                "algorithmic_gate_rejected",
            ):
                raise RuntimeError(f"v65 trusted rank {rank} has an unsafe skip reason")
            skipped.append(candidate)
    if observed_ranks != [
        int(value) for value in selection.get("formal_replay_eligible_ranks") or []
    ]:
        raise RuntimeError("v65 eligible-rank ledger is inconsistent")
    return eligible, skipped, selection


def _filter_targets() -> list[tuple[object, str, Callable[..., Any]]]:
    contract_globals = frozen.validate_replay_summary.__globals__
    return [
        (base, "validate_selection", base.validate_selection),
        (base.v10, "validate_selection", base.v10.validate_selection),
        (frozen, "validate_selection", frozen.validate_selection),
        (contract_globals, "validate_selection", contract_globals["validate_selection"]),
    ]


def _set_target(target: object, name: str, value: Callable[..., Any]) -> None:
    if isinstance(target, dict):
        target[name] = value
    else:
        setattr(target, name, value)


def run(args: argparse.Namespace) -> dict[str, Any]:
    selection_path = Path(args.selection).resolve()
    state_path = Path(args.state).resolve()
    eligible, skipped, _selection = eligibility_partition(selection_path, state_path)
    if not eligible:
        raise RuntimeError("v65 zero-eligible batch must re-explore before formal runner")
    v10.robust_v7._validate_formal_selection(selection_path)

    targets = _filter_targets()
    original_contract_validate = targets[-1][2]

    def filtered_validate(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        original = original_contract_validate(value)
        return [item for item in original if item.get("formal_replay_eligible") is True]

    original_formal_validate = v10.robust_v7._validate_formal_selection
    for target, name, _original in targets:
        _set_target(target, name, filtered_validate)
    v10.robust_v7._validate_formal_selection = lambda _path: None
    try:
        result = dict(v11.run(args))
    finally:
        for target, name, original in reversed(targets):
            _set_target(target, name, original)
        v10.robust_v7._validate_formal_selection = original_formal_validate

    physical = int(result.get("physical_invocation_count", -1))
    successful = int(result.get("successful_real_replay_count", -1))
    technical = int(result.get("technical_failure_count", -1))
    attempts = successful + technical
    if (
        physical < 0
        or physical > len(eligible)
        or successful < 0
        or technical < 0
        or attempts > len(eligible)
        or successful > physical
        or physical > attempts
    ):
        raise RuntimeError("v65 eligible formal accounting is inconsistent")

    result.update(
        {
            "robust_formal_eligibility_contract": ELIGIBILITY_CONTRACT,
            "runner_contract": RUNNER_CONTRACT,
            "eligible_source_ranks": [
                int(item["robust_formal_eligibility"]["source_rank"])
                for item in eligible
            ],
            "zero_call_skipped_source_ranks": [
                int(item["robust_formal_eligibility"]["source_rank"])
                for item in skipped
            ],
            "zero_call_skip_count": len(skipped),
            "batch_budget_attempt_count": attempts,
            "pre_invocation_technical_failure_count": attempts - physical,
            "qps_used_for_inner_scoring": False,
        }
    )
    summary_path = Path(str(result["summary_path"])).resolve()
    summary = dict(base.load_json(summary_path))
    summary.update(
        {
            "robust_formal_eligibility_contract": ELIGIBILITY_CONTRACT,
            "runner_contract": RUNNER_CONTRACT,
            "eligible_source_ranks": result["eligible_source_ranks"],
            "zero_call_skipped_source_ranks": result[
                "zero_call_skipped_source_ranks"
            ],
            "zero_call_skip_count": len(skipped),
            "batch_budget_attempt_count": attempts,
            "pre_invocation_technical_failure_count": attempts - physical,
            "qps_used_for_inner_scoring": False,
        }
    )
    temporary = summary_path.with_name(f".{summary_path.name}.v65.{os.getpid()}")
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
        raise RuntimeError("v65 eligibility runner is formal-only")
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
                "runner_contract": RUNNER_CONTRACT,
            }
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
