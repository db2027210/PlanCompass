#!/usr/bin/env python3
"""JOB v58 formal adapter with complete gradient-state transitions."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any, Mapping

import run_job_round8_strict_serial_robust_v10_resource_median as v10


PARAMETER_FIELDS = (
    "accepted_count",
    "incumbent_score",
    "incumbent_params",
    "range_by_parameter",
    "gradient_observation_count",
)
GRADIENT_CONTRACT = "job_v58_complete_gradient_state_transition_v1"
_V10_DECIDE = v10.decide_state_update


def state_subset(value: Mapping[str, Any], label: str = "state") -> dict[str, Any]:
    if any(field not in value for field in PARAMETER_FIELDS):
        raise RuntimeError(f"{label} lacks the complete v58 formal state")
    result = {field: copy.deepcopy(value[field]) for field in PARAMETER_FIELDS}
    result["gradient_observation_count"] = int(result["gradient_observation_count"])
    if result["gradient_observation_count"] < 0:
        raise RuntimeError(f"{label} has a negative gradient observation count")
    return result


def decide_state_update(
    candidate: Mapping[str, Any],
    actual: Mapping[str, Any],
    state: dict[str, Any],
    *,
    accepted_target: int = 10,
    diagnostic_only: bool = False,
) -> dict[str, Any]:
    """Wrap v10 so every role publishes the complete before/after state."""
    before = state_subset(state, "candidate state before")
    result = dict(
        _V10_DECIDE(
            candidate,
            actual,
            state,
            accepted_target=accepted_target,
            diagnostic_only=diagnostic_only,
        )
    )
    after = state_subset(state, "candidate state after")
    result["incumbent_state_before"] = before
    result["incumbent_state_after"] = after
    result["gradient_state_contract"] = GRADIENT_CONTRACT
    return result


def validate_formal_state_transitions(
    result: Mapping[str, Any],
    initial_state: Mapping[str, Any],
) -> dict[str, Any]:
    successful = result.get("successful") or []
    failures = result.get("technical_failures") or []
    if not isinstance(successful, list) or not isinstance(failures, list):
        raise RuntimeError("v58 formal transition ledgers are malformed")
    if len(successful) != int(result.get("successful_real_replay_count", -1)):
        raise RuntimeError("v58 successful transition count mismatch")
    if len(failures) != int(result.get("technical_failure_count", -1)):
        raise RuntimeError("v58 technical transition count mismatch")

    expected = state_subset(initial_state, "initial state")
    accepted_rows = 0
    observed_ranks: set[int] = set()
    for row in successful:
        if not isinstance(row, Mapping):
            raise RuntimeError("v58 successful row is not an object")
        rank = int(row.get("rank", -1))
        if rank < 1 or rank > 5 or rank in observed_ranks:
            raise RuntimeError("v58 successful rank ledger is invalid")
        observed_ranks.add(rank)
        if row.get("technical_failure") is not False:
            raise RuntimeError(f"v58 successful rank {rank} is marked technical")
        role = str(row.get("selection_role") or "")
        if role not in ("trusted", "probe"):
            raise RuntimeError(f"v58 rank {rank} has an invalid role")
        before_raw = row.get("incumbent_state_before")
        after_raw = row.get("incumbent_state_after")
        if not isinstance(before_raw, Mapping) or not isinstance(after_raw, Mapping):
            raise RuntimeError(f"v58 rank {rank} lacks complete state evidence")
        before = state_subset(before_raw, f"rank {rank} before")
        after = state_subset(after_raw, f"rank {rank} after")
        if before != expected:
            raise RuntimeError(f"v58 rank {rank} breaks the state chain")

        accepted = row.get("accepted") is True
        gradient = row.get("gradient_update_applied") is True
        incumbent = row.get("incumbent_update_applied") is True
        range_updated = row.get("range_update_applied") is True
        if not accepted:
            if gradient or incumbent or range_updated or after != before:
                raise RuntimeError(
                    f"v58 rejected/probe rank {rank} mutated formal state"
                )
        else:
            if role != "trusted" or not gradient or not incumbent or not range_updated:
                raise RuntimeError(
                    f"v58 accepted rank {rank} lacks all trusted update flags"
                )
            if int(after["accepted_count"]) != int(before["accepted_count"]) + 1:
                raise RuntimeError(f"v58 accepted rank {rank} count transition is invalid")
            if int(after["gradient_observation_count"]) != int(
                before["gradient_observation_count"]
            ) + 1:
                raise RuntimeError(f"v58 accepted rank {rank} gradient transition is invalid")
            if float(after["incumbent_score"]) <= float(before["incumbent_score"]):
                raise RuntimeError(f"v58 accepted rank {rank} did not improve incumbent")
            binding = row.get("candidate_binding") or {}
            params = binding.get("candidate_params")
            if not isinstance(params, Mapping) or dict(after["incumbent_params"]) != dict(params):
                raise RuntimeError(f"v58 accepted rank {rank} is not parameter-bound")
            expected_range = {
                name: {
                    "min": min(
                        float(before["range_by_parameter"][name]["min"]),
                        float(params[name]),
                    ),
                    "max": max(
                        float(before["range_by_parameter"][name]["max"]),
                        float(params[name]),
                    ),
                }
                for name in params
            }
            if after["range_by_parameter"] != expected_range:
                raise RuntimeError(f"v58 accepted rank {rank} range transition is invalid")
            accepted_rows += 1
        expected = after

    for row in failures:
        if not isinstance(row, Mapping):
            raise RuntimeError("v58 technical row is not an object")
        rank = int(row.get("rank", -1))
        if rank < 1 or rank > 5 or rank in observed_ranks:
            raise RuntimeError("v58 technical rank ledger is invalid")
        observed_ranks.add(rank)
        if (
            row.get("technical_failure") is not True
            or row.get("accepted") is not False
            or row.get("gradient_update_applied") is not False
            or row.get("incumbent_update_applied") is not False
            or row.get("range_update_applied") is not False
        ):
            raise RuntimeError(f"v58 technical rank {rank} has an update flag")

    raw_final = result.get("final_state")
    if isinstance(raw_final, Mapping):
        # Old frozen runners omit gradient_observation_count.  All other fields
        # must still agree before v58 supplies the proven gradient field.
        for field in PARAMETER_FIELDS[:-1]:
            if raw_final.get(field) != expected[field]:
                raise RuntimeError(f"v58 final state differs in {field}")
    if int(expected["accepted_count"]) - int(initial_state["accepted_count"]) != accepted_rows:
        raise RuntimeError("v58 accepted delta differs from accepted trusted rows")
    if int(expected["gradient_observation_count"]) - int(
        initial_state["gradient_observation_count"]
    ) != accepted_rows:
        raise RuntimeError("v58 gradient delta differs from accepted trusted rows")
    return expected


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.v58.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if bool(getattr(args, "diagnostic_only", False)):
        raise RuntimeError("v58 complete-gradient runner is formal-only")
    state_path = Path(args.state).resolve()
    initial_state = v10.base.load_json(state_path)
    # Propagate the complete-state decision hook through every adapter layer.
    v10.decide_state_update = decide_state_update
    v10.robust_v7.decide_state_update = decide_state_update
    v10.base.decide_state_update = decide_state_update
    result = dict(v10.run(args))
    final_state = validate_formal_state_transitions(result, initial_state)
    result["final_state"] = copy.deepcopy(final_state)
    result["complete_gradient_state_transition_guard"] = "passed"
    result["gradient_state_contract"] = GRADIENT_CONTRACT

    summary_path = Path(str(result["summary_path"])).resolve()
    summary = dict(v10.base.load_json(summary_path))
    summary["successful"] = copy.deepcopy(result.get("successful") or [])
    summary["final_state"] = copy.deepcopy(final_state)
    summary["complete_gradient_state_transition_guard"] = "passed"
    summary["gradient_state_contract"] = GRADIENT_CONTRACT
    _atomic_json(summary_path, summary)
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
    if args.dry_run:
        v10.robust_v7._validate_formal_selection(args.selection.resolve())
        print(
            json.dumps(
                v10.base.preflight(
                    args.selection.resolve(),
                    args.authority_root.resolve(),
                    args.observations.resolve(),
                    args.exclusion.resolve(),
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
