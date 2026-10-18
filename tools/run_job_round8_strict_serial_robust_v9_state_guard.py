#!/usr/bin/env python3
"""JOB robust-v8 runner with a fail-closed per-row state-transition guard."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Mapping

import run_job_round8_strict_serial_robust_v8_apply_failure as robust_v8


PARAMETER_FIELDS = (
    "accepted_count",
    "incumbent_score",
    "incumbent_params",
    "range_by_parameter",
)


def _state_subset(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if any(field not in value for field in PARAMETER_FIELDS):
        raise RuntimeError(f"{label} lacks the complete formal parameter state")
    return {field: copy.deepcopy(value[field]) for field in PARAMETER_FIELDS}


def validate_formal_state_transitions(
    result: Mapping[str, Any],
    initial_state: Mapping[str, Any],
) -> None:
    """Prove that only accepted trusted rows can mutate formal state."""
    successful = result.get("successful") or []
    failures = result.get("technical_failures") or []
    final_state = result.get("final_state")
    if (
        not isinstance(successful, list)
        or not isinstance(failures, list)
        or not isinstance(final_state, Mapping)
        or not all(isinstance(row, Mapping) for row in successful + failures)
    ):
        raise RuntimeError("formal result has malformed transition ledgers")
    if len(successful) != int(result.get("successful_real_replay_count", -1)):
        raise RuntimeError("formal successful transition ledger count mismatch")
    if len(failures) != int(result.get("technical_failure_count", -1)):
        raise RuntimeError("formal technical transition ledger count mismatch")

    expected = _state_subset(initial_state, "initial state")
    accepted_rows = 0
    observed_ranks: set[int] = set()
    for row in successful:
        rank = int(row.get("rank", -1))
        if rank < 1 or rank > 5 or rank in observed_ranks:
            raise RuntimeError("formal successful rank ledger is invalid")
        observed_ranks.add(rank)
        if row.get("technical_failure") is not False:
            raise RuntimeError(f"successful rank {rank} is marked technical")
        role = str(row.get("selection_role") or "")
        if role not in ("trusted", "probe"):
            raise RuntimeError(f"rank {rank} has invalid selection role")
        before_raw = row.get("incumbent_state_before")
        after_raw = row.get("incumbent_state_after")
        if not isinstance(before_raw, Mapping) or not isinstance(after_raw, Mapping):
            raise RuntimeError(f"rank {rank} lacks before/after state evidence")
        before = _state_subset(before_raw, f"rank {rank} before")
        after = _state_subset(after_raw, f"rank {rank} after")
        if before != expected:
            raise RuntimeError(f"rank {rank} state chain does not match prior row")

        accepted = row.get("accepted") is True
        gradient = row.get("gradient_update_applied") is True
        incumbent = row.get("incumbent_update_applied") is True
        range_updated = row.get("range_update_applied") is True
        if not accepted:
            if gradient or incumbent or range_updated or after != before:
                raise RuntimeError(
                    f"rejected rank {rank} mutated gradient/incumbent/range state"
                )
        else:
            if role != "trusted" or not incumbent or not range_updated:
                raise RuntimeError(
                    f"accepted rank {rank} is not a trusted incumbent/range update"
                )
            if int(after["accepted_count"]) != int(before["accepted_count"]) + 1:
                raise RuntimeError(f"accepted rank {rank} count transition is invalid")
            if float(after["incumbent_score"]) <= float(before["incumbent_score"]):
                raise RuntimeError(f"accepted rank {rank} did not improve incumbent")
            binding = row.get("candidate_binding") or {}
            candidate_params = binding.get("candidate_params")
            if (
                not isinstance(candidate_params, Mapping)
                or dict(after["incumbent_params"]) != dict(candidate_params)
            ):
                raise RuntimeError(
                    f"accepted rank {rank} incumbent parameters are not candidate-bound"
                )
            expected_range = {
                name: {
                    "min": min(
                        float(before["range_by_parameter"][name]["min"]),
                        float(candidate_params[name]),
                    ),
                    "max": max(
                        float(before["range_by_parameter"][name]["max"]),
                        float(candidate_params[name]),
                    ),
                }
                for name in candidate_params
            }
            if after["range_by_parameter"] != expected_range:
                raise RuntimeError(f"accepted rank {rank} range transition is invalid")
            accepted_rows += 1
        expected = after

    for row in failures:
        rank = int(row.get("rank", -1))
        if rank < 1 or rank > 5 or rank in observed_ranks:
            raise RuntimeError("formal technical rank ledger is invalid")
        observed_ranks.add(rank)
        if (
            row.get("technical_failure") is not True
            or row.get("accepted") is not False
            or row.get("gradient_update_applied") is not False
            or row.get("incumbent_update_applied") is not False
            or row.get("range_update_applied") is not False
        ):
            raise RuntimeError(f"technical rank {rank} has a formal update flag")

    final_subset = _state_subset(final_state, "final state")
    if final_subset != expected:
        raise RuntimeError("formal final state does not equal the per-row state chain")
    accepted_delta = int(final_subset["accepted_count"]) - int(
        _state_subset(initial_state, "initial state")["accepted_count"]
    )
    if accepted_delta != accepted_rows:
        raise RuntimeError("formal accepted delta does not equal accepted trusted rows")


def run(args: argparse.Namespace) -> dict[str, Any]:
    if bool(getattr(args, "diagnostic_only", False)):
        raise RuntimeError("robust v9 state guard is formal-only")
    state_path = Path(args.state).resolve()
    initial_state = robust_v8.base.load_json(state_path)
    result = dict(robust_v8.run(args))
    if "technical_failures" not in result:
        summary_path = Path(str(result.get("summary_path") or "")).resolve()
        if not summary_path.is_file():
            raise RuntimeError("formal result omits technical ledger and summary")
        summary = robust_v8.base.load_json(summary_path)
        result["technical_failures"] = copy.deepcopy(
            summary.get("technical_failures") or []
        )
    validate_formal_state_transitions(result, initial_state)
    result["per_row_state_transition_guard"] = "passed"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("replay_root", type=Path)
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--exclusion", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--accepted-target", type=int, default=10)
    parser.add_argument("--diagnostic-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.state is None:
        raise RuntimeError("robust v9 state guard requires --state")
    if args.dry_run:
        robust_v8.robust_v7._validate_formal_selection(args.selection.resolve())
        print(
            json.dumps(
                robust_v8.base.preflight(
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
