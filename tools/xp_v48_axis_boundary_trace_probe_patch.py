#!/usr/bin/env python3
"""XP v48: strict admission for a sealed clean-anchor axis trace probe.

v46 already validates resource and stability limits.  This patch changes only
the generation-contract predicate: the automatically generated axis trace is
accepted when every provenance, step, fingerprint, and state-neutrality field
is internally consistent.  Missing or inconsistent evidence fails closed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping


CONTRACT = "xp_v48_strict_clean_anchor_axis_boundary_trace_probe_v1"
AXIS_CONTRACT = "bounded_clean_anchor_axis_boundary_trace_v1"
EXPECTED_V46_SHA256 = "dae58a981853c0c4bddd9b62935c1164e1d8576617b07c118537ecb279f16616"
RESOURCES = ("cpu", "io", "memory")
REQUIRED_DIAGNOSTIC_FIELDS = {
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
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"XP v48 non-numeric {label}") from exc
    if not math.isfinite(number):
        raise RuntimeError(f"XP v48 non-finite {label}")
    return number


def _finite_tree(value: Any, label: str) -> None:
    if not isinstance(value, Mapping) or not value:
        raise RuntimeError(f"XP v48 missing mapping {label}")
    for key, child in value.items():
        child_label = f"{label}.{key}"
        if isinstance(child, Mapping):
            _finite_tree(child, child_label)
        else:
            _finite(child, child_label)


def _same_number(left: Any, right: Any) -> bool:
    a = _finite(left, "comparison.left")
    b = _finite(right, "comparison.right")
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)


def _same_numeric_mapping(left: Any, right: Any) -> bool:
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    if set(left) != set(right):
        return False
    for key in left:
        a = left[key]
        b = right[key]
        if isinstance(a, Mapping) or isinstance(b, Mapping):
            if not _same_numeric_mapping(a, b):
                return False
        elif not _same_number(a, b):
            return False
    return True


def _resource_vector(candidate: Mapping[str, Any], field: str) -> bool:
    vector = candidate.get(field)
    if not isinstance(vector, Mapping) or set(vector) != set(RESOURCES):
        return False
    return all(_finite(vector[name], f"{field}.{name}") >= 0.0 for name in RESOURCES)


def _strict_axis_probe(candidate: Mapping[str, Any]) -> bool:
    generation = candidate.get("safe_frontier_generation")
    if not isinstance(generation, Mapping):
        return False
    if generation.get("contract") != AXIS_CONTRACT:
        return False

    required_true = (
        "fresh_explain",
        "formal_range_eligible",
        "respects_formal_range",
        "strict_formal_range_interior",
        "vertices_included",
    )
    if not all(generation.get(field) is True for field in required_true):
        return False
    if generation.get("trusted_without_real_evidence") is not False:
        return False
    if generation.get(
        "exploration_anchor_updates_accepted_incumbent_range_or_gradient"
    ) is not False:
        return False
    if generation.get("step_limit_semantics") != (
        "per_clean_diagnostic_exploration_hop"
    ):
        return False
    if isinstance(generation.get("halton_start_index"), bool) or int(
        generation.get("halton_start_index", 0)
    ) < 1:
        return False

    configured = _finite(
        generation.get("maximum_relative_parameter_step"),
        "generation.maximum_relative_parameter_step",
    )
    observed = _finite(
        generation.get("observed_maximum_relative_parameter_step"),
        "generation.observed_maximum_relative_parameter_step",
    )
    if not (_same_number(configured, 0.8) and 0.0 < observed <= configured):
        return False

    axis = generation.get("axis_parameter")
    if not isinstance(axis, str) or not axis:
        return False
    anchor = generation.get("exploration_anchor_params")
    formal_incumbent = generation.get("formal_incumbent_params")
    initial = candidate.get("initial_params")
    params = candidate.get("candidate_params")
    optimized = candidate.get("optimized_params")
    if not all(isinstance(value, Mapping) and value for value in (
        anchor, formal_incumbent, initial, params, optimized
    )):
        return False
    if not (
        set(anchor) == set(formal_incumbent) == set(initial) == set(params) == set(optimized)
        and axis in anchor
        and _same_numeric_mapping(anchor, initial)
        and _same_numeric_mapping(params, optimized)
    ):
        return False
    for label, mapping in (
        ("anchor", anchor),
        ("formal_incumbent", formal_incumbent),
        ("candidate_params", params),
    ):
        if any(_finite(value, f"{label}.{key}") <= 0.0 for key, value in mapping.items()):
            return False

    delta = candidate.get("delta_param")
    if not isinstance(delta, Mapping) or set(delta) != set(params):
        return False
    nonzero: list[str] = []
    for name, record in delta.items():
        if not isinstance(record, Mapping):
            return False
        before = _finite(record.get("before"), f"delta.{name}.before")
        after = _finite(record.get("after_clip"), f"delta.{name}.after_clip")
        absolute = _finite(record.get("absolute"), f"delta.{name}.absolute")
        relative = _finite(record.get("relative"), f"delta.{name}.relative")
        proposed = _finite(
            record.get("proposed_before_clip"), f"delta.{name}.proposed_before_clip"
        )
        if not (
            _same_number(before, anchor[name])
            and _same_number(after, params[name])
            and _same_number(absolute, after - before)
            and _same_number(relative, absolute / before)
            and record.get("clipped") is False
            and _same_number(proposed, after)
        ):
            return False
        if not _same_number(relative, 0.0):
            nonzero.append(name)
    if nonzero != [axis] or not _same_number(abs(float(delta[axis]["relative"])), observed):
        return False
    if not _same_number(candidate.get("maximum_relative_parameter_step"), observed):
        return False

    if candidate.get("candidate_origin") != "safe_frontier_axis_halton_explain_probe":
        return False
    if candidate.get("probe_kind") != "maximin_uncertain_probe":
        return False
    if candidate.get("selection_role") != "maximin_uncertain_probe":
        return False
    if candidate.get("formal_range_eligible") is not True:
        return False
    if candidate.get("boundary_corner") is not False:
        return False
    if candidate.get("near_corner_excluded_from_noncorner_gate") is not False:
        return False
    if candidate.get("used_for_gradient") is not False:
        return False
    if candidate.get("used_for_inner_incumbent_update") is not False:
        return False
    if candidate.get("active_subset") != [axis]:
        return False
    if set(candidate.get("diagnostic_fields_preserved") or []) != REQUIRED_DIAGNOSTIC_FIELDS:
        return False
    if candidate.get("eta_applied_to_candidate") is not True:
        return False
    # The clean anchor may itself have come from either an adaptive gradient
    # trace or an incumbent-to-corner ray.  The axis probe does not apply that
    # source eta again (eta == 0); it seals the source eta and semantics.
    if candidate.get("eta_semantics") not in {
        "adaptive_plan_change_gradient_direction_multiplier",
        "incumbent_to_corner_ray_fraction",
    }:
        return False
    if not _same_number(candidate.get("eta"), 0.0):
        return False

    sealed_t = generation.get("sealed_source_T")
    sealed_confidence = generation.get("sealed_source_confidence")
    sealed_dscore = generation.get("sealed_source_dScore_dU")
    sealed_gradient = generation.get("sealed_source_g_j")
    for label, value in (
        ("sealed_source_T", sealed_t),
        ("sealed_source_confidence", sealed_confidence),
        ("sealed_source_dScore_dU", sealed_dscore),
        ("sealed_source_g_j", sealed_gradient),
        ("T", candidate.get("T")),
        ("confidence", candidate.get("confidence")),
        ("dScore/dU", candidate.get("dScore/dU")),
        ("g_j", candidate.get("g_j")),
    ):
        _finite_tree(value, label)
    source_eta = _finite(generation.get("sealed_source_eta"), "sealed_source_eta")
    if not (0.0 < source_eta <= configured):
        return False
    if not _same_numeric_mapping(candidate.get("g_j"), sealed_gradient):
        return False
    for field, sealed in (
        ("T", sealed_t),
        ("confidence", sealed_confidence),
        ("dScore/dU", sealed_dscore),
    ):
        actual = candidate.get(field)
        if not isinstance(actual, Mapping) or not set(actual).issubset(set(sealed)):
            return False
        if any(not _same_numeric_mapping({key: actual[key]}, {key: sealed[key]}) for key in actual):
            return False

    if not all(_resource_vector(candidate, field) for field in (
        "feature_ratio", "predicted_resource", "utilization"
    )):
        return False
    _finite(candidate.get("candidate_score"), "candidate_score")

    fingerprint = candidate.get("three_level_fingerprint")
    if not isinstance(fingerprint, Mapping):
        return False
    identifier = str(fingerprint.get("id") or "")
    plan = str(fingerprint.get("plan_level") or "")
    if not (
        fingerprint.get("contract") == "plan_feature_resource_three_level_v1"
        and fingerprint.get("valid") is True
        and fingerprint.get("validation_errors") == []
        and fingerprint.get("parameter_fallback_used") is False
        and identifier.startswith("three-level-sha256:")
        and plan.startswith("ratio-plan-sha256:")
        and candidate.get("workload_plan_fingerprint") == plan
    ):
        return False
    for level in ("feature_ratio_level", "predicted_resource_level"):
        value = fingerprint.get(level)
        if not isinstance(value, Mapping) or set(value) != set(RESOURCES):
            return False
        if any(isinstance(value[name], bool) or not isinstance(value[name], int) for name in RESOURCES):
            return False

    formula = fingerprint.get("resource_formula_audit")
    final_resource = candidate.get("final_resource")
    if not isinstance(formula, Mapping) or set(formula) != set(RESOURCES):
        return False
    if not isinstance(final_resource, Mapping) or set(final_resource) != set(RESOURCES):
        return False
    for resource in RESOURCES:
        audit = formula[resource]
        if not isinstance(audit, Mapping):
            return False
        required = {
            "absolute_error", "allowed_error", "baseline_real",
            "expected_resource", "feature_ratio", "observed_resource",
        }
        if set(audit) != required:
            return False
        values = {key: _finite(audit[key], f"formula.{resource}.{key}") for key in required}
        if not (
            values["absolute_error"] >= 0.0
            and values["allowed_error"] >= 0.0
            and values["absolute_error"] <= values["allowed_error"]
            and _same_number(values["expected_resource"], values["baseline_real"] * values["feature_ratio"])
            and _same_number(values["observed_resource"], values["expected_resource"])
            and _same_number(values["observed_resource"], final_resource[resource])
        ):
            return False
    return True


def axis_boundary_trace_safe(candidate: Mapping[str, Any]) -> bool:
    """Return False, rather than partially admitting, on any malformed field."""

    try:
        return _strict_axis_probe(candidate)
    except (KeyError, TypeError, ValueError, RuntimeError, ZeroDivisionError):
        return False


def install_v46_probe_acceptance(v46_module: Any) -> Any:
    """Extend v46's predicate for exactly one strictly checked contract."""

    source = Path(v46_module.__file__).resolve()
    if _sha256(source) != EXPECTED_V46_SHA256:
        raise RuntimeError("XP v48 v46 patch SHA mismatch")
    if getattr(v46_module._fresh_safe_probe, "_xp_v48_contract", None) == CONTRACT:
        return v46_module
    original: Callable[[Mapping[str, Any]], bool] = v46_module._fresh_safe_probe

    def fresh_safe_probe(candidate: Mapping[str, Any]) -> bool:
        generation = candidate.get("safe_frontier_generation") or {}
        if generation.get("contract") == AXIS_CONTRACT:
            return axis_boundary_trace_safe(candidate)
        return original(candidate)

    fresh_safe_probe._xp_v48_contract = CONTRACT  # type: ignore[attr-defined]
    v46_module._fresh_safe_probe = fresh_safe_probe
    v46_module.XP_V48_AXIS_PROBE_CONTRACT = CONTRACT
    return v46_module


def seal_ready_probe5(
    v46_module: Any,
    bundle_module: Any,
    manifest: Mapping[str, Any],
    selection: Mapping[str, Any],
    excluded_ids: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Revalidate and annotate a sealed probe5 without rebuilding candidates."""

    install_v46_probe_acceptance(v46_module)
    fixed = copy.deepcopy(dict(selection))
    top5 = list(fixed.get("top5") or [])
    identifiers = [bundle_module.fingerprint_id(item) for item in top5]
    discovery = copy.deepcopy(dict(manifest.get("diagnostic_discovery") or {}))
    frontier = discovery.get("safe_frontier_contract") or {}
    safe_upper = dict(frontier.get("maximum_predicted_utilization") or {})
    risk_audit = frontier.get("stability_risk_audit") or {}
    excluded = {str(value) for value in excluded_ids}
    axis_candidates = [
        item for item in top5
        if (item.get("safe_frontier_generation") or {}).get("contract") == AXIS_CONTRACT
    ]
    counts = manifest.get("counts") or {}
    checks = {
        "manifest_ready": manifest.get("status") == "ready" and not (manifest.get("input_gaps") or []),
        "novel_noncorner_pool_at_least_five": int(counts.get("novel_non_corner_fingerprints", -1)) >= 5,
        "exactly_five": len(top5) == 5,
        "unique_three_level_fingerprints": len(identifiers) == 5 and len(set(identifiers)) == 5,
        "excluded_fingerprints_absent": not (set(identifiers) & excluded),
        "all_uncertain_probe": all(item.get("selection_role") == "maximin_uncertain_probe" for item in top5),
        "axis_contract_present_exactly_twice": len(axis_candidates) == 2,
        "axis_contract_strictly_valid": all(axis_boundary_trace_safe(item) for item in axis_candidates),
        "all_fresh_safe_noncorner": all(
            v46_module._fresh_safe_probe(item)
            and bundle_module._is_probe(item)
            and bundle_module._safe_probe(item, safe_upper)
            and bundle_module._stability_risk_safe(item, risk_audit)
            for item in top5
        ),
        "no_gradient_or_incumbent_use": all(
            item.get("used_for_gradient") is False
            and item.get("used_for_inner_incumbent_update") is False
            for item in top5
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"XP v48 sealed probe5 audit failed: {checks}")

    v46_checks = {
        key: checks[key]
        for key in (
            "exactly_five", "unique_three_level_fingerprints",
            "excluded_fingerprints_absent", "all_uncertain_probe",
            "all_fresh_safe_noncorner", "no_gradient_or_incumbent_use",
        )
    }
    v46_audit = {
        "contract": v46_module.CONTRACT,
        "source_patch": "v48_sha_bound_zero_call_axis_contract_compatibility",
        "checks": v46_checks,
        "source_safe_probe_count": int(counts["safe_probe_candidates"]),
        "source_safe_transition_count": int(counts["safe_transition_backtracking_candidates"]),
        "selected_fingerprint_ids": identifiers,
        "selection_algorithm": "existing_deterministic_parameter_space_maximin_v1",
        "diagnostic_only": True,
        "first_probe_replay_cannot_be_formal": True,
        "all_candidates_remain_uncertain_probes": True,
        "pending_trust_requires_clean_exact_point_repeat": True,
        "formal_requires_trusted3_plus_probe2_and_all_entry_gates": True,
        "resource_envelope_relaxed": False,
        "parameter_step_limit_relaxed": False,
        "trusted_promotion_applied": False,
        "manual_candidate_injection": False,
        "formal_state_mutation": False,
        "new_workload_calls": 0,
    }
    v48_audit = {
        "contract": CONTRACT,
        "checks": checks,
        "axis_generation_contract": AXIS_CONTRACT,
        "axis_fingerprint_ids": [bundle_module.fingerprint_id(item) for item in axis_candidates],
        "source_selection_reused": True,
        "candidate_regeneration": False,
        "manual_candidate_injection": False,
        "resource_envelope_relaxed": False,
        "parameter_step_limit_relaxed": False,
        "formal_state_mutation": False,
        "new_workload_calls": 0,
    }
    discovery["transition_backtracking_candidates_preferred"] = False
    discovery["transition_shortfall_policy"] = v46_module.CONTRACT
    discovery["v46_safe_pool_routing"] = copy.deepcopy(v46_audit)
    discovery["v48_axis_boundary_trace_probe"] = copy.deepcopy(v48_audit)
    fixed["batch_mode"] = "diagnostic_validation"
    fixed["replay_authorized"] = True
    fixed["diagnostic_discovery"] = copy.deepcopy(discovery)
    fixed["v46_safe_pool_routing"] = copy.deepcopy(v46_audit)
    fixed["v48_axis_boundary_trace_probe"] = copy.deepcopy(v48_audit)
    return fixed, checks


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
