#!/usr/bin/env python3
"""JOB strict runner adapter with three-observation median acceptance.

The frozen v6 runner remains responsible for PostgreSQL parameter application,
the serial max-five replay contract, accounting, and baseline restoration.  This
adapter changes only formal trusted-point acceptance: two sealed diagnostic
observations are verified before any workload call and combined with the new
formal observation using a median.  Diagnostic runs and uncertain probes stay
state-neutral.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import sys
from types import ModuleType
from typing import Any, Mapping


POLICY_CONTRACT = "job_three_independent_exact_point_median_acceptance_v2"
BASE_RUNNER_NAME = "run_job_round8_strict_serial_base_v6.py"
_POLICY_CACHE: dict[str, list[float]] = {}


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_base() -> ModuleType:
    configured = os.environ.get("JOB_ROUND8_BASE_RUNNER")
    base_path = (
        Path(configured).resolve()
        if configured
        else Path(__file__).with_name(BASE_RUNNER_NAME)
    )
    if not base_path.is_file():
        raise RuntimeError(f"missing frozen JOB base runner: {base_path}")
    spec = importlib.util.spec_from_file_location(
        "job_round8_strict_serial_base_v6",
        base_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen JOB base runner")
    module = importlib.util.module_from_spec(spec)
    inserted = str(base_path.parent)
    sys.path.insert(0, inserted)
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.path and sys.path[0] == inserted:
            sys.path.pop(0)
    return module


base = load_base()
_base_decide_state_update = base.decide_state_update


def _params(candidate: Mapping[str, Any]) -> dict[str, float]:
    value = candidate.get("candidate_params")
    if not isinstance(value, Mapping) or set(value) != set(base.PARAMETER_NAMES):
        raise RuntimeError("robust candidate has incomplete candidate_params")
    return {
        name: float(base.finite(value[name], f"candidate_params.{name}"))
        for name in base.PARAMETER_NAMES
    }


def _same_params(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(
        math.isclose(
            float(left[name]),
            float(right[name]),
            rel_tol=0.0,
            abs_tol=max(1e-15, abs(float(left[name])) * 1e-12),
        )
        for name in base.PARAMETER_NAMES
    )


def _selection_candidates(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates = value.get("top5") or value.get("candidates")
    if not isinstance(candidates, list):
        raise RuntimeError("sealed prior selection has no Top-5")
    return [item for item in candidates if isinstance(item, Mapping)]


def _summary_rows(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = value.get("successful")
    if not isinstance(rows, list):
        raise RuntimeError("sealed prior summary has no successful rows")
    return [item for item in rows if isinstance(item, Mapping)]


def validate_policy(candidate: Mapping[str, Any]) -> list[float]:
    policy = candidate.get("robust_acceptance_policy")
    if not isinstance(policy, Mapping) or policy.get("contract") != POLICY_CONTRACT:
        raise RuntimeError("formal trusted candidate lacks robust acceptance policy")
    observations = policy.get("prior_observations")
    if not isinstance(observations, list) or len(observations) != 2:
        raise RuntimeError("robust policy must seal exactly two prior observations")
    params = _params(candidate)
    params_sha = canonical_sha256(params)
    if policy.get("candidate_params_sha256") != params_sha:
        raise RuntimeError("robust policy candidate-parameter SHA mismatch")
    cache_key = canonical_sha256(policy)
    if cache_key in _POLICY_CACHE:
        return list(_POLICY_CACHE[cache_key])

    scores: list[float] = []
    summary_paths: set[str] = set()
    summary_hashes: set[str] = set()
    for index, raw in enumerate(observations, 1):
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"robust prior observation {index} is not an object")
        selection_path = Path(str(raw.get("selection_path") or "")).resolve()
        summary_path = Path(str(raw.get("summary_path") or "")).resolve()
        expected_selection_sha = str(raw.get("selection_sha256") or "")
        expected_summary_sha = str(raw.get("summary_sha256") or "")
        if (
            not selection_path.is_file()
            or not summary_path.is_file()
            or len(expected_selection_sha) != 64
            or len(expected_summary_sha) != 64
            or file_sha256(selection_path) != expected_selection_sha
            or file_sha256(summary_path) != expected_summary_sha
        ):
            raise RuntimeError(f"robust prior observation {index} SHA mismatch")
        if str(summary_path) in summary_paths or expected_summary_sha in summary_hashes:
            raise RuntimeError("robust prior observations are not independent")
        summary_paths.add(str(summary_path))
        summary_hashes.add(expected_summary_sha)

        selection = base.load_json(selection_path)
        summary = base.load_json(summary_path)
        matching_candidates = [
            item
            for item in _selection_candidates(selection)
            if isinstance(item.get("candidate_params"), Mapping)
            and _same_params(params, item["candidate_params"])
        ]
        matching_rows = []
        for row in _summary_rows(summary):
            binding = row.get("candidate_binding")
            row_params = (
                binding.get("candidate_params")
                if isinstance(binding, Mapping)
                else None
            )
            if (
                isinstance(row_params, Mapping)
                and _same_params(params, row_params)
                and row.get("technical_failure") is False
            ):
                matching_rows.append(row)
        if len(matching_candidates) != 1 or len(matching_rows) != 1:
            raise RuntimeError(
                f"robust prior observation {index} parameter binding is ambiguous"
            )
        score = float(base.finite(matching_rows[0].get("actual_score"), "prior actual_score"))
        sealed_score = float(base.finite(raw.get("actual_score"), "sealed actual_score"))
        if not math.isclose(score, sealed_score, rel_tol=0.0, abs_tol=1e-12):
            raise RuntimeError(f"robust prior observation {index} score mismatch")
        scores.append(score)

    _POLICY_CACHE[cache_key] = list(scores)
    return scores


def decide_state_update(
    candidate: Mapping[str, Any],
    actual: Mapping[str, Any],
    state: dict[str, Any],
    *,
    accepted_target: int = 10,
    diagnostic_only: bool = False,
) -> dict[str, Any]:
    if diagnostic_only or candidate.get("selection_role") == "probe":
        return _base_decide_state_update(
            candidate,
            actual,
            state,
            accepted_target=accepted_target,
            diagnostic_only=diagnostic_only,
        )

    prior_scores = validate_policy(candidate)
    raw_score = float(base.actual_score(actual))
    independent_scores = [*prior_scores, raw_score]
    robust_score = float(statistics.median(independent_scores))
    eligible = bool(
        candidate.get("selection_role") == "trusted"
        and candidate.get("incumbent_update_eligible") is True
        and candidate.get("used_for_inner_incumbent_update") is True
    )
    gate = candidate.get("algorithmic_gate")
    gate_allows = not (
        isinstance(gate, Mapping) and gate.get("accepted") is False
    )
    accepted = bool(
        eligible
        and gate_allows
        and state["accepted_count"] < accepted_target
        and robust_score > float(state["incumbent_score"]) + 1e-12
    )
    gradient_update = bool(accepted and candidate.get("used_for_gradient") is True)
    before = base._state_subset(state)
    if accepted:
        if gradient_update:
            state["gradient_observation_count"] = int(
                state.get("gradient_observation_count", 0)
            ) + 1
        new_params = _params(candidate)
        old_range = state["range_by_parameter"]
        state["incumbent_score"] = robust_score
        state["incumbent_params"] = new_params
        state["accepted_count"] += 1
        state["range_by_parameter"] = {
            name: {
                "min": min(float(old_range[name]["min"]), new_params[name]),
                "max": max(float(old_range[name]["max"]), new_params[name]),
            }
            for name in base.PARAMETER_NAMES
        }
    after = base._state_subset(state)
    return {
        # The frozen runner consumes this field for state mutation.  The
        # adapter rewrites the public summary to retain the raw third score and
        # publishes this value separately as robust_acceptance_score.
        "actual_score": robust_score,
        "accepted": accepted,
        "gradient_update_applied": gradient_update,
        "incumbent_update_applied": accepted,
        "range_update_applied": accepted,
        "incumbent_state_before": before,
        "incumbent_state_after": after,
    }


base.decide_state_update = decide_state_update


def _validate_formal_selection(selection_path: Path) -> None:
    selection = base.load_json(selection_path)
    candidates = base.validate_selection(selection)
    roles = [str(item.get("selection_role") or "") for item in candidates]
    if roles != ["trusted", "trusted", "trusted", "probe", "probe"]:
        raise RuntimeError("robust formal selection must be trusted3+probe2")
    if selection.get("three_observation_acceptance_contract") != POLICY_CONTRACT:
        raise RuntimeError("robust formal selection contract is missing")
    for candidate in candidates[:3]:
        validate_policy(candidate)


def run(args: argparse.Namespace) -> dict[str, Any]:
    diagnostic_only = bool(getattr(args, "diagnostic_only", False))
    if not diagnostic_only:
        _validate_formal_selection(Path(args.selection).resolve())
    result = dict(base.run(args))
    if diagnostic_only:
        return result

    summary_path = Path(str(result["summary_path"])).resolve()
    summary = dict(base.load_json(summary_path))
    successful = summary.get("successful") or []
    robust_audit = []
    for row in successful:
        if not isinstance(row, dict) or row.get("selection_role") != "trusted":
            continue
        robust_score = float(base.finite(row.get("actual_score"), "robust score"))
        raw_score = float(base.actual_score(row["actual_utilization"]))
        row["raw_actual_score"] = raw_score
        row["robust_acceptance_score"] = robust_score
        row["actual_score"] = raw_score
        row["acceptance_score_source"] = POLICY_CONTRACT
        robust_audit.append(
            {
                "rank": int(row["rank"]),
                "raw_third_observation_score": raw_score,
                "robust_acceptance_score": robust_score,
                "accepted": bool(row.get("accepted")),
            }
        )
    summary["three_observation_acceptance_contract"] = POLICY_CONTRACT
    summary["robust_acceptance_audit"] = robust_audit
    temporary = summary_path.with_name(f".{summary_path.name}.robust.{os.getpid()}")
    temporary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, summary_path)
    result["successful"] = successful
    result["robust_acceptance_audit"] = robust_audit
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
    if args.dry_run:
        if not args.diagnostic_only:
            _validate_formal_selection(args.selection.resolve())
        print(
            json.dumps(
                base.preflight(
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
