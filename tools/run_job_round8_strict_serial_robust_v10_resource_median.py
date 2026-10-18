#!/usr/bin/env python3
"""JOB formal runner using two diagnostics plus one formal resource median."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import statistics
from typing import Any, Mapping

import run_job_round8_strict_serial_robust_v9_state_guard as v9


robust_v8 = v9.robust_v8
robust_v7 = robust_v8.robust_v7
base = robust_v8.base
CAPABILITY = {"cpu": 3.0, "io": 256.0, "memory": 2.91015625}
RESOURCES = ("cpu", "io", "memory")
POLICY_CONTRACT = "job_three_independent_exact_point_resource_median_rescored_v3"
LEGACY_POLICY_CONTRACT = robust_v7.POLICY_CONTRACT
_EVIDENCE_CACHE: dict[str, list[dict[str, Any]]] = {}


def _prior_evidence(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    policy = candidate.get("robust_acceptance_policy")
    if not isinstance(policy, Mapping) or policy.get("contract") != LEGACY_POLICY_CONTRACT:
        raise RuntimeError("trusted candidate lacks two sealed diagnostic observations")
    cache_key = robust_v7.canonical_sha256(policy)
    if cache_key in _EVIDENCE_CACHE:
        return copy.deepcopy(_EVIDENCE_CACHE[cache_key])
    # Reuse the old verifier for independent paths, hashes, exact parameters,
    # and sealed scores, then additionally bind exact plan and resources.
    verified_scores = robust_v7.validate_policy(candidate)
    params = robust_v7._params(candidate)
    candidate_plan = str(
        (candidate.get("three_level_fingerprint") or {}).get("plan_level") or ""
    )
    if not candidate_plan:
        raise RuntimeError("trusted candidate has no exact plan regime")
    evidence: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(policy["prior_observations"], 1):
        selection_path = Path(str(raw["selection_path"])).resolve()
        summary_path = Path(str(raw["summary_path"])).resolve()
        selection = base.load_json(selection_path)
        summary = base.load_json(summary_path)
        candidates = [
            item
            for item in robust_v7._selection_candidates(selection)
            if isinstance(item.get("candidate_params"), Mapping)
            and robust_v7._same_params(params, item["candidate_params"])
        ]
        rows = []
        for row in robust_v7._summary_rows(summary):
            binding = row.get("candidate_binding") or {}
            row_params = binding.get("candidate_params")
            if (
                isinstance(row_params, Mapping)
                and robust_v7._same_params(params, row_params)
                and row.get("technical_failure") is False
            ):
                rows.append(row)
        if len(candidates) != 1 or len(rows) != 1:
            raise RuntimeError("diagnostic resource binding is ambiguous")
        observed_plan = str(
            (candidates[0].get("three_level_fingerprint") or {}).get("plan_level") or ""
        )
        resource = rows[0].get("actual_resource")
        utilization = rows[0].get("actual_utilization")
        if (
            observed_plan != candidate_plan
            or not isinstance(resource, Mapping)
            or not isinstance(utilization, Mapping)
        ):
            raise RuntimeError("diagnostic exact-plan resource evidence is incomplete")
        normalized_resource = {}
        for name in RESOURCES:
            amount = float(base.finite(resource.get(name), f"diagnostic {ordinal} resource.{name}"))
            observed_u = float(base.finite(utilization.get(name), f"diagnostic {ordinal} utilization.{name}"))
            expected_u = amount / CAPABILITY[name]
            if not math.isclose(observed_u, expected_u, rel_tol=1e-9, abs_tol=1e-12):
                raise RuntimeError("diagnostic resource/utilization evidence is inconsistent")
            normalized_resource[name] = amount
        evidence.append(
            {
                "ordinal": ordinal,
                "actual_resource": normalized_resource,
                "actual_score": float(verified_scores[ordinal - 1]),
                "selection_path": str(selection_path),
                "selection_sha256": robust_v7.file_sha256(selection_path),
                "summary_path": str(summary_path),
                "summary_sha256": robust_v7.file_sha256(summary_path),
                "plan_level": observed_plan,
            }
        )
    _EVIDENCE_CACHE[cache_key] = copy.deepcopy(evidence)
    return evidence


def decide_state_update(
    candidate: Mapping[str, Any],
    actual: Mapping[str, Any],
    state: dict[str, Any],
    *,
    accepted_target: int = 10,
    diagnostic_only: bool = False,
) -> dict[str, Any]:
    if diagnostic_only or candidate.get("selection_role") == "probe":
        return robust_v7._base_decide_state_update(
            candidate,
            actual,
            state,
            accepted_target=accepted_target,
            diagnostic_only=diagnostic_only,
        )
    prior = _prior_evidence(candidate)
    raw_resource = {
        name: float(base.finite(actual.get(name), f"formal utilization.{name}"))
        * CAPABILITY[name]
        for name in RESOURCES
    }
    robust_resource = {
        name: float(
            statistics.median(
                [prior[0]["actual_resource"][name], prior[1]["actual_resource"][name], raw_resource[name]]
            )
        )
        for name in RESOURCES
    }
    robust_utilization = {
        name: robust_resource[name] / CAPABILITY[name] for name in RESOURCES
    }
    robust_score = float(base.actual_score(robust_utilization))
    raw_score = float(base.actual_score(actual))
    eligible = bool(
        candidate.get("selection_role") == "trusted"
        and candidate.get("incumbent_update_eligible") is True
        and candidate.get("used_for_inner_incumbent_update") is True
    )
    gate = candidate.get("algorithmic_gate")
    gate_allows = not (isinstance(gate, Mapping) and gate.get("accepted") is False)
    accepted = bool(
        eligible
        and gate_allows
        and int(state["accepted_count"]) < accepted_target
        and robust_score > float(state["incumbent_score"]) + 1e-12
    )
    gradient_update = bool(accepted and candidate.get("used_for_gradient") is True)
    before = base._state_subset(state)
    if accepted:
        if gradient_update:
            state["gradient_observation_count"] = int(state.get("gradient_observation_count", 0)) + 1
        params = robust_v7._params(candidate)
        old_range = state["range_by_parameter"]
        state["incumbent_score"] = robust_score
        state["incumbent_params"] = params
        state["accepted_count"] += 1
        state["range_by_parameter"] = {
            name: {
                "min": min(float(old_range[name]["min"]), params[name]),
                "max": max(float(old_range[name]["max"]), params[name]),
            }
            for name in base.PARAMETER_NAMES
        }
    after = base._state_subset(state)
    return {
        "actual_score": robust_score,
        "raw_actual_score": raw_score,
        "robust_actual_resource": robust_resource,
        "robust_actual_utilization": robust_utilization,
        "accepted": accepted,
        "gradient_update_applied": gradient_update,
        "incumbent_update_applied": accepted,
        "range_update_applied": accepted,
        "incumbent_state_before": before,
        "incumbent_state_after": after,
    }


robust_v7.decide_state_update = decide_state_update
base.decide_state_update = decide_state_update


def _rewrite_resource_audit(
    result: Mapping[str, Any], selection_path: Path
) -> dict[str, Any]:
    rewritten = dict(result)
    summary_path = Path(str(rewritten["summary_path"])).resolve()
    summary = dict(base.load_json(summary_path))
    selection_path = selection_path.resolve()
    if (
        not selection_path.is_file()
        or summary.get("selection_sha256") != robust_v7.file_sha256(selection_path)
    ):
        raise RuntimeError("formal summary is not bound to the explicit selection")
    selection = base.load_json(selection_path)
    audits = []
    successful = summary.get("successful") or []
    for row in successful:
        if not isinstance(row, dict) or row.get("selection_role") != "trusted":
            continue
        binding = row.get("candidate_binding") or {}
        params = binding.get("candidate_params")
        if not isinstance(params, Mapping):
            raise RuntimeError("trusted formal row lacks parameter binding")
        # The v8 public rewrite retained the robust score separately.
        robust_score = float(base.finite(row.get("robust_acceptance_score"), "robust acceptance score"))
        matches = [
            item for item in robust_v7._selection_candidates(selection)
            if isinstance(item.get("candidate_params"), Mapping)
            and robust_v7._same_params(params, item["candidate_params"])
        ]
        if len(matches) != 1:
            raise RuntimeError("formal robust resource candidate binding is ambiguous")
        candidate = matches[0]
        prior = _prior_evidence(candidate)
        raw_resource = {
            name: float(base.finite(row["actual_resource"][name], f"formal resource.{name}"))
            for name in RESOURCES
        }
        robust_resource = {
            name: float(statistics.median([prior[0]["actual_resource"][name], prior[1]["actual_resource"][name], raw_resource[name]]))
            for name in RESOURCES
        }
        robust_utilization = {name: robust_resource[name] / CAPABILITY[name] for name in RESOURCES}
        rescored = float(base.actual_score(robust_utilization))
        if not math.isclose(rescored, robust_score, rel_tol=0.0, abs_tol=1e-12):
            raise RuntimeError("formal robust resource and acceptance score diverged")
        row["robust_actual_resource"] = robust_resource
        row["robust_actual_utilization"] = robust_utilization
        row["robust_acceptance_score"] = rescored
        row["acceptance_score_source"] = POLICY_CONTRACT
        audits.append(
            {
                "rank": int(row["rank"]),
                "role": "trusted",
                "aggregation": "componentwise_median",
                "source_actual_resources": [prior[0]["actual_resource"], prior[1]["actual_resource"], raw_resource],
                "robust_actual_resource": robust_resource,
                "robust_actual_utilization": robust_utilization,
                "raw_formal_score": float(row["actual_score"]),
                "robust_acceptance_score": rescored,
                "accepted": bool(row.get("accepted")),
                "qps_used_for_inner_scoring": False,
            }
        )
    # Keep the controller's admission-family field stable; the material v57
    # acceptance semantics are sealed separately and are what the audit uses.
    summary["three_observation_acceptance_contract"] = LEGACY_POLICY_CONTRACT
    summary["resource_median_acceptance_contract"] = POLICY_CONTRACT
    summary["robust_resource_acceptance_audit"] = audits
    temporary = summary_path.with_name(f".{summary_path.name}.robust-v10.{os.getpid()}")
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, summary_path)
    rewritten["successful"] = successful
    rewritten["three_observation_acceptance_contract"] = LEGACY_POLICY_CONTRACT
    rewritten["resource_median_acceptance_contract"] = POLICY_CONTRACT
    rewritten["robust_resource_acceptance_audit"] = audits
    return rewritten


def run(args: argparse.Namespace) -> dict[str, Any]:
    if bool(getattr(args, "diagnostic_only", False)):
        raise RuntimeError("robust v10 resource-median runner is formal-only")
    robust_v7._validate_formal_selection(Path(args.selection).resolve())
    initial_state = base.load_json(Path(args.state).resolve())
    result = dict(robust_v8.run(args))
    if "technical_failures" not in result:
        summary = base.load_json(Path(str(result["summary_path"])).resolve())
        result["technical_failures"] = copy.deepcopy(summary.get("technical_failures") or [])
    v9.validate_formal_state_transitions(result, initial_state)
    return _rewrite_resource_audit(result, Path(args.selection))


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
        raise RuntimeError("robust v10 requires --state")
    if args.dry_run:
        robust_v7._validate_formal_selection(args.selection.resolve())
        print(json.dumps(base.preflight(args.selection.resolve(), args.authority_root.resolve(), args.observations.resolve(), args.exclusion.resolve()), indent=2, sort_keys=True))
        return
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
