#!/usr/bin/env python3
"""Build a bounded-exploration TPC-H successor from diagnostic evidence.

Trusted replays may refresh their exact structural plan-regime calibration.
A complete five-of-five, zero-technical discovery batch may additionally
validate previously unknown exact regimes for resource calibration only.
Discovery probes never update gradient, incumbent, range, or accepted state.
The builder performs no workload call and no state mutation.  A clean first
observation is prioritized for an exact-point validation retest before any new
regime is explored.  Retests are explicitly non-novel and formal-state neutral.
If a normal batch cannot contain trusted3+probe2, construction fails closed.
Unknown regimes remain probes, but their resource prediction uses a sealed
hierarchical residual prior learned only from earlier clean diagnostics.  A
separate bounded acquisition score preserves finite exploration without
presenting an optimistic raw estimate as the candidate's expected utility.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Dict, Iterable, List, Mapping, Sequence


sys.path.insert(0, str(Path(__file__).resolve().parent))
import tpch_round19_plan_regime_select as selector


RESOURCES = ("cpu", "io", "memory")
PARAMETER_BOUNDS = {
    "cpu_index_tuple_cost": (0.0005, 0.05),
    "cpu_operator_cost": (0.00025, 0.025),
    "cpu_tuple_cost": (0.001, 0.1),
    "parallel_setup_cost": (0.0, 10000.0),
    "parallel_tuple_cost": (0.001, 1.0),
    "random_page_cost": (0.1, 100.0),
    "seq_page_cost": (0.1, 100.0),
}
REQUIRED_FIELDS = (
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

MAX_UNKNOWN_UCB_BONUS = 0.05
MINIMUM_TRUSTED_OBSERVATIONS = 2


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError(f"{label} must be finite")
    return result


def empirical_quantile(values: Sequence[float], probability: float) -> float:
    """Return a deterministic linearly interpolated empirical quantile."""
    ordered = sorted(finite(value, "quantile") for value in values)
    if not ordered:
        raise RuntimeError("empirical quantile requires evidence")
    position = min(1.0, max(0.0, probability)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def hierarchical_probe_prior(
    registry: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Aggregate prediction residuals without granting exact-regime trust."""
    rows: Dict[tuple[str, int, str], Mapping[str, Any]] = {}
    for calibration in registry.values():
        for evidence in calibration.get("evidence") or []:
            if not isinstance(evidence, Mapping):
                continue
            key = (
                str(evidence.get("source_summary_sha256") or ""),
                int(evidence.get("source_rank") or -1),
                str(evidence.get("source_fingerprint") or ""),
            )
            rows[key] = evidence
    if not rows:
        raise RuntimeError("hierarchical probe prior requires clean evidence")
    scalar_samples: Dict[str, List[float]] = {name: [] for name in RESOURCES}
    excluded_nonpositive_scalars = 0
    for evidence in rows.values():
        scalar = evidence.get("scalar_by_resource") or {}
        for name in RESOURCES:
            value = finite(scalar[name], f"hierarchical_scalar.{name}")
            if value <= 0.0:
                excluded_nonpositive_scalars += 1
                continue
            scalar_samples[name].append(value)
    if any(not values for values in scalar_samples.values()):
        raise RuntimeError("hierarchical probe prior lacks positive residuals")
    return {
        "contract": "unknown_regime_hierarchical_residual_prior_v1",
        "evidence_count": len(rows),
        "positive_scalar_count_by_resource": {
            name: len(values) for name, values in scalar_samples.items()
        },
        "excluded_nonpositive_scalar_count": excluded_nonpositive_scalars,
        "scalar_median": {
            name: empirical_quantile(values, 0.5)
            for name, values in scalar_samples.items()
        },
        "scalar_lower": {
            name: empirical_quantile(values, 0.1)
            for name, values in scalar_samples.items()
        },
        "scalar_upper": {
            name: empirical_quantile(values, 0.9)
            for name, values in scalar_samples.items()
        },
        "exact_regime_trust_granted": False,
        "optimizer_state_updated": False,
    }


def hierarchical_calibrate_probe(
    candidate: Mapping[str, Any],
    prior: Mapping[str, Any],
) -> Dict[str, Any]:
    """Attach an expected score and a separate UCB to an unknown regime."""
    item = copy.deepcopy(dict(candidate))
    raw = item.get("predicted_resource")
    if not isinstance(raw, Mapping):
        raise RuntimeError("unknown probe lacks predicted_resource")
    median = prior.get("scalar_median") or {}
    corrected = {
        name: finite(raw[name], f"unknown_raw.{name}")
        * finite(median[name], f"hierarchical_median.{name}")
        for name in RESOURCES
    }
    expected_score, expected_utilization = selector.score_resource(corrected)
    raw_score, _ = selector.score_resource(
        {name: finite(raw[name], f"unknown_raw.{name}") for name in RESOURCES}
    )
    scalar_choices = [
        (
            finite((prior.get("scalar_lower") or {})[name], f"lower.{name}"),
            finite((prior.get("scalar_median") or {})[name], f"median.{name}"),
            finite((prior.get("scalar_upper") or {})[name], f"upper.{name}"),
        )
        for name in RESOURCES
    ]
    plausible_scores = []
    for scalars in itertools.product(*scalar_choices):
        resource = {
            name: finite(raw[name], f"unknown_raw.{name}") * scalars[index]
            for index, name in enumerate(RESOURCES)
        }
        plausible_scores.append(selector.score_resource(resource)[0])
    # Raw structural optimism was observed to overstate current-epoch TPC-H
    # scores materially while the hierarchical expected score stayed close to
    # reality.  Cap optimism and use it only to order candidates whose expected
    # score independently clears the feasibility floor.
    residual_envelope_upper = max(plausible_scores)
    optimistic_hypothesis = max(raw_score, residual_envelope_upper)
    acquisition_score = expected_score + min(
        MAX_UNKNOWN_UCB_BONUS,
        max(0.0, optimistic_hypothesis - expected_score),
    )
    calibration = {
        "scalar_by_resource": {
            name: corrected[name] / finite(raw[name], f"unknown_raw.{name}")
            for name in RESOURCES
        }
    }
    item.update(
        {
            "precalibration_predicted_resource": {
                name: float(raw[name]) for name in RESOURCES
            },
            "predicted_resource": corrected,
            "final_resource": copy.deepcopy(corrected),
            "candidate_score": expected_score,
            "final_score": expected_score,
            "predicted_score": expected_score,
            "resource_utility_score": expected_score,
            "utilization": expected_utilization,
            "probe_acquisition_score": acquisition_score,
            "probe_acquisition_contract": (
                "hierarchical_expected_plus_bounded_bonus_ucb_v3"
            ),
            "probe_structural_model_score": raw_score,
            "probe_residual_envelope_upper_score": residual_envelope_upper,
            "hierarchical_probe_prior": copy.deepcopy(dict(prior)),
            "confidence": {
                "exact_plan_regime": 0.0,
                "measurement_count": 0,
                "hierarchical_prior_evidence_count": int(
                    prior.get("evidence_count") or 0
                ),
                "score_expected": expected_score,
                "score_acquisition_upper": acquisition_score,
                "score_acquisition_bonus_cap": MAX_UNKNOWN_UCB_BONUS,
                "structural_model_score": raw_score,
                "residual_envelope_upper_score": residual_envelope_upper,
                "unknown_regime": True,
            },
            "resource_prediction_contract": (
                "unknown_regime_hierarchical_residual_median_v1"
            ),
            "score_prediction_contract": (
                "unknown_regime_expected_score_plus_bounded_ucb_v2"
            ),
        }
    )
    item["three_level_fingerprint"] = selector.recalibrated_fingerprint(
        candidate,
        corrected,
        calibration,
    )
    item["three_level_fingerprint"]["post_prediction_calibration"] = (
        "unknown_regime_hierarchical_probe_prior_v1"
    )
    return item


def formal_incumbent_plan_probe(
    candidate: Mapping[str, Any],
) -> Dict[str, Any]:
    """Keep the sealed incumbent calibration for a plan-preserving probe.

    A hierarchical residual learned from unrelated plan regimes must not be
    applied to a candidate whose full workload plan fingerprint is identical
    to the measured formal incumbent.  The point remains an uncertain probe
    until it receives a real observation; this function changes prediction
    provenance, not trust or optimizer state.
    """
    item = copy.deepcopy(dict(candidate))
    raw = item.get("predicted_resource")
    anchor = item.get("formal_incumbent_anchor")
    if not (
        item.get("incumbent_exact_plan_preserved") is True
        and isinstance(raw, Mapping)
        and isinstance(anchor, Mapping)
        and anchor.get("manual_candidate_injection") is False
    ):
        raise RuntimeError("invalid formal incumbent plan-preserving probe")
    resource = {name: finite(raw[name], f"incumbent_raw.{name}") for name in RESOURCES}
    score, utilization = selector.score_resource(resource)
    item.update(
        {
            "predicted_resource": resource,
            "final_resource": copy.deepcopy(resource),
            "candidate_score": score,
            "final_score": score,
            "predicted_score": score,
            "resource_utility_score": score,
            "utilization": utilization,
            "probe_acquisition_score": score,
            "probe_acquisition_contract": (
                "sealed_formal_incumbent_exact_plan_score_v1"
            ),
            "confidence": {
                "exact_plan_regime": 0.0,
                "measurement_count": 0,
                "unknown_regime": True,
                "incumbent_exact_plan_preserved": True,
                "score_expected": score,
                "score_acquisition_upper": score,
            },
            "resource_prediction_contract": (
                "sealed_formal_incumbent_same_exact_plan_calibration_v1"
            ),
            "score_prediction_contract": (
                "same_exact_plan_expected_score_probe_only_v1"
            ),
            "hierarchical_probe_prior_bypassed": True,
            "hierarchical_probe_prior_bypass_reason": (
                "full_workload_plan_fingerprint_matches_formal_incumbent"
            ),
        }
    )
    return item


def probe_rank_score(candidate: Mapping[str, Any]) -> float:
    """Use UCB only for probe acquisition, never as expected candidate score."""
    return finite(
        candidate.get("probe_acquisition_score", candidate["candidate_score"]),
        "probe_rank_score",
    )


def fingerprint(candidate: Mapping[str, Any]) -> str:
    value = candidate.get("three_level_fingerprint")
    if isinstance(value, Mapping):
        value = value.get("id")
    identifier = str(value or "")
    if not identifier.startswith("three-level-sha256:"):
        raise RuntimeError("candidate has no valid three-level fingerprint")
    return identifier


def parameter_key(candidate: Mapping[str, Any]) -> str:
    return selector.parameter_key(candidate)


def actual_parameter_interior(candidate: Mapping[str, Any]) -> bool:
    """Validate an old-format candidate directly against the fixed bounds."""
    parameters = candidate.get("candidate_params")
    if not isinstance(parameters, Mapping):
        return False
    try:
        return all(
            lower < finite(parameters[name], f"candidate_params.{name}") < upper
            for name, (lower, upper) in PARAMETER_BOUNDS.items()
        )
    except (KeyError, TypeError, ValueError, RuntimeError):
        return False


def exact_regime(candidate: Mapping[str, Any]) -> str:
    computed = selector.structural_plan_regime_id(candidate)
    declared = candidate.get("structural_plan_regime_id")
    if declared is not None and str(declared) != computed:
        raise RuntimeError("declared/computed exact plan regime mismatch")
    return computed


def validate_previous(
    selection: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    top5 = selection.get("top5")
    successful = summary.get("successful")
    if not isinstance(top5, list) or len(top5) != 5:
        raise RuntimeError("previous selection must contain five candidates")
    if (
        not isinstance(successful, list)
        or len(successful) != 5
        or int(summary.get("physical_call_count", -1)) != 5
        or int(summary.get("valid_replay_count", -1)) != 5
        or int(summary.get("technical_failure_count", -1)) != 0
        or summary.get("baseline_restored") is not True
        or summary.get("accepted_update_applied") is not False
        or summary.get("incumbent_update_applied") is not False
        or summary.get("range_update_applied") is not False
        or summary.get("gradient_update_applied") is not False
    ):
        raise RuntimeError("previous batch is not a clean diagnostic phys5/valid5")
    by_rank = {int(item["rank"]): item for item in top5}
    if set(by_rank) != set(range(1, 6)):
        raise RuntimeError("previous selection ranks must be 1..5")
    postrobust = summary.get("postrobust_reseal") or {}
    resealed = bool(
        isinstance(postrobust, Mapping)
        and postrobust.get("contract")
        == "tpch_three_observation_postrobust_reseal_v1"
        and postrobust.get("new_physical_calls") == 0
        and postrobust.get("state_neutral") is True
        and postrobust.get("resealed_selection_sha256")
        == summary.get("selection_sha256")
    )
    rows_by_parameter: Dict[str, List[Mapping[str, Any]]] = {}
    for row in successful:
        rows_by_parameter.setdefault(parameter_key(row), []).append(row)
    normalized = []
    for rank in range(1, 6):
        source = by_rank[rank]
        matching_rows = rows_by_parameter.get(parameter_key(source), [])
        if len(matching_rows) != 1:
            raise RuntimeError(
                f"previous result parameter binding count is "
                f"{len(matching_rows)}: rank={rank}"
            )
        row = matching_rows[0]
        if (
            row.get("technical_failure") is not False
            or row.get("valid_replay") is not True
            or parameter_key(row) != parameter_key(source)
            or (
                not resealed
                and (
                    int(row["rank"]) != rank
                    or str(row.get("selection_role"))
                    != str(source.get("selection_role"))
                    or fingerprint(row) != fingerprint(source)
                )
            )
        ):
            raise RuntimeError(f"previous result binding mismatch: rank={rank}")
        actual_resource = {
            name: finite(row["actual_resource"][name], f"actual.{name}")
            for name in RESOURCES
        }
        if any(value <= 0.0 for value in actual_resource.values()):
            raise RuntimeError(f"non-positive actual resource: rank={rank}")
        normalized.append(
            {
                **copy.deepcopy(dict(row)),
                "rank": rank,
                "selection_role": str(source.get("selection_role")),
                "three_level_fingerprint": copy.deepcopy(
                    source.get("three_level_fingerprint")
                ),
                "actual_resource": actual_resource,
                "actual_score": finite(row["actual_score"], "actual_score"),
                "_source": source,
            }
        )
    return sorted(normalized, key=lambda item: int(item["rank"]))


def update_registry(
    selection: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
    summary_sha256: str,
) -> tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    source_registry = copy.deepcopy(selection.get("validated_plan_regimes") or {})
    # Older exact-repeat selections keyed calibration by ratio-plan IDs and
    # stored no structural-regime evidence list.  Those entries cannot be
    # applied to a structural plan regime and must not be treated as trusted.
    # The clean current diagnostic below revalidates its five exact structural
    # regimes; all other legacy entries remain unknown probes.
    registry = {
        str(regime): copy.deepcopy(calibration)
        for regime, calibration in source_registry.items()
        if isinstance(calibration, Mapping)
        and isinstance(calibration.get("evidence"), list)
        and calibration.get("evidence")
    }
    diagnostic_selection = selection.get("diagnostic_only") is True
    feedback = []
    for result in results:
        source = result["_source"]
        role = str(source.get("selection_role"))
        regime = exact_regime(source)
        # Any clean diagnostic replay can validate an exact structural regime
        # for resource calibration.  Probe evidence remains strictly excluded
        # from accepted/incumbent/range/gradient state.
        used = role == "trusted" or (
            role == "probe" and diagnostic_selection
        )
        optimizer_state_used = False
        raw = (
            source.get("precalibration_predicted_resource")
            or source.get("calibration_input_resource")
            or source.get("predicted_resource")
        )
        if not isinstance(raw, Mapping):
            raise RuntimeError("source candidate lacks resource prediction")
        scalar = {
            name: finite(result["actual_resource"][name], f"actual.{name}")
            / finite(raw[name], f"prediction.{name}")
            for name in RESOURCES
        }
        feedback.append(
            {
                "rank": int(result["rank"]),
                "selection_role": role,
                "structural_plan_regime_id": regime,
                "used_for_exact_regime_calibration": used,
                "used_for_optimizer_state": optimizer_state_used,
                "probe_updates_forbidden": not optimizer_state_used,
                "actual_score": float(result["actual_score"]),
                "actual_resource": copy.deepcopy(result["actual_resource"]),
                "raw_predicted_resource": {
                    name: float(raw[name]) for name in RESOURCES
                },
                "scalar_by_resource": scalar,
            }
        )
        if not used:
            continue
        if regime not in registry:
            if not diagnostic_selection or (
                role == "trusted"
                and source.get("exact_plan_regime_validated") is not True
            ):
                raise RuntimeError(
                    f"trusted exact regime is not validated: {regime}"
                )
            registry[regime] = {
                "structural_plan_regime_id": regime,
                "evidence": [],
                "validated_by": (
                    "clean_exact_plan_diagnostic_phys5_valid5_zero_technical_v1"
                ),
                "optimizer_state_updated": False,
                "accepted_updated": False,
                "incumbent_updated": False,
                "range_updated": False,
                "gradient_updated": False,
            }
        registry[regime].setdefault("evidence", []).append(
            {
                "event": "tpch_autonomous_diagnostic_replay_complete",
                "source_rank": int(result["rank"]),
                "source_fingerprint": fingerprint(source),
                "source_parameter_key": parameter_key(source),
                "ratio_plan_level": source.get("workload_plan_fingerprint"),
                "predicted_resource": {
                    name: float(raw[name]) for name in RESOURCES
                },
                "actual_resource": copy.deepcopy(result["actual_resource"]),
                "scalar_by_resource": scalar,
                "actual_score": float(result["actual_score"]),
                "source_summary_sha256": summary_sha256,
            }
        )
    for regime, calibration in registry.items():
        evidence = list(calibration.get("evidence") or [])
        if not evidence:
            raise RuntimeError(f"validated regime lacks evidence: {regime}")
        resource_values = {
            name: [
                finite(row["actual_resource"][name], name)
                for row in evidence
            ]
            for name in RESOURCES
        }
        resources_are_positive = all(
            min(values) > 0.0 for values in resource_values.values()
        )
        maximum_resource_factor = (
            max(max(values) / min(values) for values in resource_values.values())
            if resources_are_positive
            else None
        )
        actual_score_span = (
            max(finite(row["actual_score"], "actual_score") for row in evidence)
            - min(finite(row["actual_score"], "actual_score") for row in evidence)
        )
        trusted_prediction_eligible = bool(
            len(evidence) >= MINIMUM_TRUSTED_OBSERVATIONS
            and actual_score_span <= 0.05
            and maximum_resource_factor is not None
            and maximum_resource_factor <= 1.5
        )
        calibration.update(
            {
                "structural_plan_regime_id": regime,
                "evidence_count": len(evidence),
                "scalar_by_resource": {
                    name: statistics.median(
                        finite(row["scalar_by_resource"][name], name)
                        for row in evidence
                    )
                    for name in RESOURCES
                },
                "actual_score_median": statistics.median(
                    finite(row["actual_score"], "actual_score")
                    for row in evidence
                ),
                "actual_resource_median": {
                    name: statistics.median(
                        finite(row["actual_resource"][name], name)
                        for row in evidence
                    )
                    for name in RESOURCES
                },
                "actual_score_span": actual_score_span,
                "maximum_resource_factor": maximum_resource_factor,
                "minimum_trusted_observations": MINIMUM_TRUSTED_OBSERVATIONS,
                "trusted_prediction_eligible": trusted_prediction_eligible,
                "evidence_contract": (
                    "exact_workload_plan_regime_clean_observation_"
                    "componentwise_median_v2"
                ),
                "global_scalar_forbidden": True,
                "unknown_regime_calibration_forbidden": True,
            }
        )
    return registry, feedback


def first_observation_retest(
    selection: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any] | None:
    """Build a formal-neutral exact-point retest after a probe5 discovery.

    A repeat validates the same parameter points independently.  Its derived
    fingerprint may change after replacing the resource estimate with the
    first real observation, but it is explicitly excluded from novelty counts.
    """
    source = selection.get("top5") or []
    if (
        selection.get("diagnostic_only") is not True
        or len(source) != 5
        or any(str(item.get("selection_role")) != "probe" for item in source)
    ):
        return None
    by_parameter = {parameter_key(row): row for row in results}
    if len(by_parameter) != 5:
        raise RuntimeError("first-observation retest requires five exact points")
    repeated: List[Dict[str, Any]] = []
    for raw in source:
        regime = exact_regime(raw)
        calibration = registry.get(regime) or {}
        evidence = list(calibration.get("evidence") or [])
        if len(evidence) != 1:
            return None
        observed = by_parameter.get(parameter_key(raw))
        if observed is None:
            raise RuntimeError("retest source/result parameter binding mismatch")
        candidate = copy.deepcopy(dict(raw))
        actual_resource = {
            name: finite(observed["actual_resource"][name], f"actual.{name}")
            for name in RESOURCES
        }
        actual_score = finite(observed["actual_score"], "actual_score")
        corrected_score, utilization = selector.score_resource(actual_resource)
        fingerprint_calibration = {
            "scalar_by_resource": {
                name: actual_resource[name]
                / finite(raw["predicted_resource"][name], f"predicted.{name}")
                for name in RESOURCES
            }
        }
        source_fingerprint = fingerprint(raw)
        candidate.update(
            {
                "pre_retest_predicted_resource": copy.deepcopy(
                    raw.get("predicted_resource") or {}
                ),
                "pre_retest_candidate_score": raw.get("candidate_score"),
                "predicted_resource": actual_resource,
                "final_resource": copy.deepcopy(actual_resource),
                "utilization": utilization,
                "candidate_score": actual_score,
                "predicted_score": actual_score,
                "final_score": actual_score,
                "resource_utility_score": corrected_score,
                "probe_acquisition_score": actual_score,
                "structural_plan_regime_id": regime,
                "exact_plan_regime_validated": False,
                "candidate_trust_basis": (
                    "one_observation_exact_point_pending_independent_retest"
                ),
                "confidence": {
                    "exact_plan_regime": 0.5,
                    "measurement_count": 1,
                    "score_lower": actual_score,
                    "score_median": actual_score,
                    "score_upper": actual_score,
                    "unknown_regime": False,
                    "independent_retest_required": True,
                },
                "prediction_interval": {
                    "lower": actual_score,
                    "median": actual_score,
                    "upper": actual_score,
                    "observation_count": 1,
                    "contract": "exact_regime_one_observation_interval_v1",
                },
                "three_level_fingerprint": selector.recalibrated_fingerprint(
                    raw,
                    actual_resource,
                    fingerprint_calibration,
                ),
                "source_fingerprint_id": source_fingerprint,
                "validation_repeat": True,
                "novel_fingerprint": False,
                "counts_toward_novel_fingerprint_gate": False,
                "resource_prediction_contract": (
                    "exact_regime_first_observation_retest_v1"
                ),
                "score_prediction_contract": (
                    "exact_regime_first_observation_retest_v1"
                ),
            }
        )
        candidate["exact_plan_regime_id"] = selector.exact_plan_regime_id(
            candidate
        )
        repeated.append(candidate)

    ranked = sorted(
        repeated,
        key=lambda item: (-float(item["candidate_score"]), parameter_key(item)),
    )
    trusted_keys = {parameter_key(ranked[index]) for index in (0, 2, 4)}
    trusted = [item for item in repeated if parameter_key(item) in trusted_keys]
    probes = [item for item in repeated if parameter_key(item) not in trusted_keys]
    trusted.sort(key=lambda item: float(item["candidate_score"]), reverse=True)
    probes.sort(key=lambda item: float(item["candidate_score"]), reverse=True)
    top5 = [*trusted, *probes]
    if len(trusted) != 3 or len(probes) != 2:
        raise RuntimeError("exact-point retest is not trusted3+probe2")
    for rank, candidate in enumerate(top5, 1):
        is_trusted = rank <= 3
        candidate.update(
            {
                "rank": rank,
                "predicted_rank": rank,
                "selection_role": "trusted" if is_trusted else "probe",
                "role": "trusted" if is_trusted else "uncertain_probe",
                "trusted": is_trusted,
                "uncertain_probe": not is_trusted,
                "diagnostic_only": True,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "range_update_allowed": False,
                "gradient_update_allowed": False,
                "incumbent_update_eligible": False,
                "eligible_for_best_candidate": False,
                "used_for_gradient": False,
                "used_for_inner_incumbent_update": False,
                "qps_used_for_inner_scoring": False,
            }
        )
    identifiers = [fingerprint(item) for item in top5]
    if len(set(identifiers)) != 5:
        raise RuntimeError("exact-point retest fingerprints are not unique")
    return {
        "top5": top5,
        "ranked_candidates": top5,
        "validated_plan_regimes": copy.deepcopy(dict(registry)),
        "retest_source_fingerprints": [fingerprint(item) for item in source],
    }


def recalibrate(
    candidate: Mapping[str, Any],
    calibration: Mapping[str, Any],
) -> Dict[str, Any]:
    item = copy.deepcopy(dict(candidate))
    raw = item.get("predicted_resource")
    if not isinstance(raw, Mapping):
        raise RuntimeError("candidate lacks predicted_resource")
    empirical = calibration.get("actual_resource_median")
    if not isinstance(empirical, Mapping):
        raise RuntimeError("validated exact regime lacks empirical resource median")
    corrected = {
        name: finite(empirical[name], f"actual_resource_median.{name}")
        for name in RESOURCES
    }
    resource_utility_score, utilization = selector.score_resource(corrected)
    empirical_score = finite(
        calibration.get("actual_score_median"),
        "actual_score_median",
    )
    evidence = list(calibration.get("evidence") or [])
    actual_scores = [
        finite(row.get("actual_score"), "evidence.actual_score")
        for row in evidence
    ]
    if not actual_scores:
        raise RuntimeError("validated exact regime lacks score evidence")
    score_lower = min(actual_scores)
    score_upper = max(actual_scores)
    candidate_specific_calibration = copy.deepcopy(dict(calibration))
    candidate_specific_calibration["scalar_by_resource"] = {
        name: corrected[name] / finite(raw[name], f"raw.{name}")
        for name in RESOURCES
    }
    item.update(
        {
            "precalibration_predicted_resource": {
                name: float(raw[name]) for name in RESOURCES
            },
            "predicted_resource": corrected,
            "final_resource": copy.deepcopy(corrected),
            "candidate_score": empirical_score,
            "final_score": empirical_score,
            "predicted_score": empirical_score,
            "resource_utility_score": resource_utility_score,
            "utilization": utilization,
            "structural_plan_regime_id": exact_regime(item),
            "exact_plan_regime_validated": True,
            "regime_calibration": copy.deepcopy(dict(calibration)),
            "resource_prediction_contract": (
                "exact_plan_regime_empirical_componentwise_median_v1"
            ),
            "score_prediction_contract": (
                "exact_plan_regime_empirical_score_median_interval_v1"
            ),
            "prediction_interval": {
                "lower": score_lower,
                "upper": score_upper,
                "width": score_upper - score_lower,
            },
            "confidence": {
                "exact_plan_regime": 1.0,
                "measurement_count": int(
                    calibration.get("evidence_count") or 0
                ),
                "score_median": empirical_score,
                "score_lower": score_lower,
                "score_upper": score_upper,
                "actual_score_span": float(
                    calibration.get("actual_score_span") or 0.0
                ),
            },
            "three_level_fingerprint": selector.recalibrated_fingerprint(
                item, corrected, candidate_specific_calibration
            ),
        }
    )
    item["exact_plan_regime_id"] = selector.exact_plan_regime_id(item)
    return item


def diverse_take(
    candidates: Iterable[Mapping[str, Any]],
    count: int,
) -> List[Dict[str, Any]]:
    chosen: List[Dict[str, Any]] = []
    seen_regimes: set[str] = set()
    deferred: List[Mapping[str, Any]] = []
    for candidate in candidates:
        regime = exact_regime(candidate)
        if regime in seen_regimes:
            deferred.append(candidate)
            continue
        chosen.append(copy.deepcopy(dict(candidate)))
        seen_regimes.add(regime)
        if len(chosen) == count:
            return chosen
    for candidate in deferred:
        chosen.append(copy.deepcopy(dict(candidate)))
        if len(chosen) == count:
            return chosen
    return chosen


def stability_probe_take(
    candidates: Iterable[Mapping[str, Any]],
    count: int,
) -> List[Dict[str, Any]]:
    """Prefer regime diversity while keeping every replay point novel.

    The fixed contract requires five distinct three-level fingerprints, not
    five distinct plan regimes.  A discovery pool can legitimately contain
    fewer than five plan regimes, so select one point per regime first and
    then admit a second point from an already represented regime.  Parameters
    and three-level fingerprints remain unique in every pass.
    """
    eligible = []
    for candidate in candidates:
        non_corner = candidate.get("actual_parameter_noncorner_audit") or {}
        explicit_non_corner = (
            non_corner.get("reason")
            == "actual_parameter_space_noncorner"
        )
        equivalent_non_corner = actual_parameter_interior(candidate)
        if (
            candidate.get("boundary_corner") is True
            or candidate.get("boundary_plateau") is True
            or not (explicit_non_corner or equivalent_non_corner)
        ):
            continue
        confidence = candidate.get("confidence") or {}
        measurement_count = int(confidence.get("measurement_count") or 0)
        priority = (
            0 if measurement_count == 1
            else 1 if measurement_count == 0
            else 2
        )
        eligible.append(
            (
                priority,
                -probe_rank_score(candidate),
                copy.deepcopy(dict(candidate)),
            )
        )
    eligible.sort(key=lambda item: item[:2])
    chosen: List[Dict[str, Any]] = []
    regime_counts: Dict[str, int] = {}
    seen_fingerprints: set[str] = set()
    seen_parameters: set[str] = set()
    for maximum_per_regime in (1, 2, None):
        for _, _, candidate in eligible:
            regime = exact_regime(candidate)
            identifier = fingerprint(candidate)
            parameters = parameter_key(candidate)
            if identifier in seen_fingerprints or parameters in seen_parameters:
                continue
            if (
                maximum_per_regime is not None
                and regime_counts.get(regime, 0) >= maximum_per_regime
            ):
                continue
            chosen.append(candidate)
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
            seen_fingerprints.add(identifier)
            seen_parameters.add(parameters)
            if len(chosen) == count:
                return chosen
    return chosen


def score_ladder_probe_take(
    candidates: Iterable[Mapping[str, Any]],
    count: int,
    target_score: float,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Select deterministic non-corner probes along a feasible score ladder.

    Feasibility is decided before plan-regime diversity.  The previous v7
    ordering put a new regime ahead of distance to the score target, which
    could select a diverse but uniformly sub-incumbent Top-5 even when the
    pool contained enough candidates above the target.  Diversity remains a
    deterministic tie-breaker; it must never override the acquisition floor.
    """
    eligible = []
    for candidate in candidates:
        non_corner = candidate.get("actual_parameter_noncorner_audit") or {}
        explicit_non_corner = (
            non_corner.get("reason") == "actual_parameter_space_noncorner"
        )
        if (
            candidate.get("boundary_corner") is True
            or candidate.get("boundary_plateau") is True
            or not (explicit_non_corner or actual_parameter_interior(candidate))
        ):
            continue
        eligible.append(copy.deepcopy(dict(candidate)))
    if not eligible:
        return [], {
            "contract": "remaining_accepts_score_ladder_v1",
            "target_score": target_score,
            "eligible_count": 0,
            "manual_candidate_injection": False,
        }

    frontier = max(float(item["candidate_score"]) for item in eligible)
    feasible = [
        item
        for item in eligible
        if float(item["candidate_score"]) >= target_score
    ]
    if len(feasible) < count:
        return [], {
            "contract": "feasible_remaining_accepts_score_ladder_v2",
            "target_score": target_score,
            "credible_frontier_score": frontier,
            "eligible_count": len(eligible),
            "feasible_count": len(feasible),
            "required_count": count,
            "failure_reason": "insufficient_candidates_at_or_above_target",
            "manual_candidate_injection": False,
        }
    eligible = feasible
    upper = max(frontier, target_score)
    if count == 1:
        targets = [upper]
    else:
        targets = [
            upper - (upper - target_score) * index / (count - 1)
            for index in range(count)
        ]
    selected: List[Dict[str, Any]] = []
    seen_fingerprints: set[str] = set()
    seen_parameters: set[str] = set()
    regime_counts: Dict[str, int] = {}
    target_bindings = []
    for target in targets:
        choices = []
        for candidate in eligible:
            identifier = fingerprint(candidate)
            parameters = parameter_key(candidate)
            if identifier in seen_fingerprints or parameters in seen_parameters:
                continue
            regime = exact_regime(candidate)
            score = float(candidate["candidate_score"])
            # Score feasibility was enforced for the entire pool above.  Bind
            # each rung by score distance first; exact-regime diversity is a
            # tie-breaker and therefore cannot displace a materially better
            # acquisition candidate.
            key = (
                abs(score - target),
                0 if regime_counts.get(regime, 0) == 0 else 1,
                -score,
                parameters,
            )
            choices.append((key, candidate))
        if not choices:
            break
        _, chosen = min(choices, key=lambda item: item[0])
        selected.append(chosen)
        identifier = fingerprint(chosen)
        parameters = parameter_key(chosen)
        regime = exact_regime(chosen)
        seen_fingerprints.add(identifier)
        seen_parameters.add(parameters)
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
        target_bindings.append(
            {
                "target_score": target,
                "selected_score": float(chosen["candidate_score"]),
                "selected_acquisition_score": probe_rank_score(chosen),
                "structural_plan_regime_id": regime,
                "three_level_fingerprint": identifier,
            }
        )
    return selected, {
        "contract": "feasible_remaining_accepts_score_ladder_v2",
        "target_score": target_score,
        "credible_frontier_score": frontier,
        "eligible_count": len(eligible),
        "feasible_count": len(feasible),
        "target_bindings": target_bindings,
        "manual_candidate_injection": False,
    }


def stability_separated_trusted_take(
    candidates: Sequence[Mapping[str, Any]],
    count: int,
    minimum_interval_gap: float,
    minimum_lower_score: float,
) -> List[Dict[str, Any]]:
    """Choose separated repeated regimes whose LCB clears the incumbent gate."""
    best_by_regime: Dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        confidence = candidate.get("confidence") or {}
        interval = candidate.get("prediction_interval") or {}
        if (
            int(confidence.get("measurement_count") or 0) < 2
            or not math.isfinite(float(interval.get("lower", math.nan)))
            or not math.isfinite(float(interval.get("upper", math.nan)))
            or float(interval.get("lower", -math.inf)) < minimum_lower_score
        ):
            continue
        regime = exact_regime(candidate)
        current = best_by_regime.get(regime)
        if current is None or float(candidate["candidate_score"]) > float(
            current["candidate_score"]
        ):
            best_by_regime[regime] = candidate
    best_choice: (
        tuple[float, float, float, tuple[Mapping[str, Any], ...]] | None
    ) = None
    for combination in itertools.combinations(best_by_regime.values(), count):
        ordered = tuple(
            sorted(
                combination,
                key=lambda item: float(item["candidate_score"]),
                reverse=True,
            )
        )
        interval_gaps = [
            float(higher["prediction_interval"]["lower"])
            - float(lower["prediction_interval"]["upper"])
            for higher, lower in zip(ordered, ordered[1:])
        ]
        minimum_gap = min(interval_gaps, default=-math.inf)
        spread = (
            float(ordered[0]["candidate_score"])
            - float(ordered[-1]["candidate_score"])
        )
        ranking_key = (
            minimum_gap,
            spread,
            sum(float(item["candidate_score"]) for item in ordered),
            ordered,
        )
        if best_choice is None or ranking_key[:3] > best_choice[:3]:
            best_choice = ranking_key
    if best_choice is None or best_choice[0] < minimum_interval_gap:
        return []
    return [copy.deepcopy(dict(item)) for item in best_choice[3]]


def build(
    selection_path: Path,
    summary_path: Path,
    expansion_path: Path,
    output_path: Path,
    additional_exclusion_paths: Sequence[Path] = (),
    minimum_score_span: float = 0.0,
    resume_state_path: Path | None = None,
) -> Dict[str, Any]:
    if output_path.exists():
        raise FileExistsError(output_path)
    selection = load(selection_path)
    summary = load(summary_path)
    expansion = load(expansion_path)
    if (
        summary.get("selection_sha256") is not None
        and summary.get("selection_sha256") != sha256(selection_path)
    ):
        raise RuntimeError("previous summary/selection SHA binding mismatch")
    results = validate_previous(selection, summary)
    registry, feedback = update_registry(selection, results, sha256(summary_path))
    pending_retest = first_observation_retest(selection, results, registry)
    resume_state: Dict[str, Any] | None = None
    resume_ranges: Mapping[str, Mapping[str, Any]] | None = None
    incumbent_score: float | None = None
    remaining_accepts = 10
    if resume_state_path is not None:
        resume_state = load(resume_state_path)
        resume_ranges = resume_state.get("range_state")
        if not (
            resume_state.get("schema_version")
            == "tpch_exact_plan_regime_formal_state_v1"
            and resume_state.get("status") == "complete"
            and resume_state.get("baseline_restored") is True
            and int(resume_state.get("technical_failure_count", -1)) == 0
            and isinstance(resume_ranges, Mapping)
            and set(resume_ranges) == set(PARAMETER_BOUNDS)
        ):
            raise RuntimeError("resume state is not a clean formal range authority")
        incumbent_score = finite(
            resume_state.get("incumbent_score"),
            "resume_state.incumbent_score",
        )
        remaining_accepts = max(
            1,
            10 - int(resume_state.get("accepted_count", 0)),
        )

    if pending_retest is not None:
        output = {
            "schema_version": "tpch_autonomous_successor_top5_v2",
            "selection_contract": (
                "exact_regime_first_observation_retest_trusted3_probe2_v1"
            ),
            "diagnostic_only": True,
            "formal_continuation": False,
            "accepted_count_start": int(summary["accepted_count"]),
            "accepted_shrink_target": 10,
            "maximum_real_replay_invocations": 5,
            "serial_real_replay_required": True,
            "technical_failures_count_toward_physical_cap": True,
            "technical_failures_count_as_valid_or_accepted": False,
            "rejected_updates_forbidden": True,
            "accepted_update_allowed": False,
            "incumbent_update_allowed": False,
            "range_update_allowed": False,
            "gradient_update_allowed": False,
            "qps_used_for_inner_scoring": False,
            "unknown_plan_regime_trusted_allowed": False,
            "trusted_slots": 3,
            "probe_slots": 2,
            "automatic_recovery_mode": "exact_point_validation_retest",
            "automatic_recovery_reason": (
                "five_exact_regimes_have_one_observation_each"
            ),
            "novel_unique_fingerprint_count": 0,
            "validation_repeat_unique_fingerprint_count": 5,
            "repeat_fingerprint_reuse_allowed": True,
            "repeat_counts_toward_novelty": False,
            "formal_state_update_allowed": False,
            "previous_batch_feedback_audit": feedback,
            "source_evidence": {
                "selection": {
                    "path": str(selection_path),
                    "sha256": sha256(selection_path),
                },
                "summary": {
                    "path": str(summary_path),
                    "sha256": sha256(summary_path),
                },
                "expansion_not_used_for_retest": {
                    "path": str(expansion_path),
                    "sha256": sha256(expansion_path),
                },
                "resume_state": (
                    {
                        "path": str(resume_state_path),
                        "sha256": sha256(resume_state_path),
                        "accepted_count": int(resume_state["accepted_count"]),
                    }
                    if resume_state_path is not None and resume_state is not None
                    else None
                ),
            },
            "selection_audit": {
                "selected_unique_fingerprint_count": 5,
                "novel_fingerprint_count": 0,
                "validation_repeat_count": 5,
                "trusted_count": 3,
                "probe_count": 2,
                "new_unknown_regime_count": 0,
            },
            **pending_retest,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(output, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return output

    probe_prior = hierarchical_probe_prior(registry)

    excluded_fingerprints = {
        fingerprint(item) for item in selection.get("top5") or []
    }
    excluded_parameters = {
        parameter_key(item) for item in selection.get("top5") or []
    }
    additional_exclusions = []
    for path in additional_exclusion_paths:
        payload = load(path)
        candidates = payload.get("top5")
        if not isinstance(candidates, list) or len(candidates) != 5:
            raise RuntimeError(
                f"additional exclusion must contain an exact Top-5: {path}"
            )
        excluded_fingerprints.update(fingerprint(item) for item in candidates)
        excluded_parameters.update(parameter_key(item) for item in candidates)
        additional_exclusions.append(
            {
                "path": str(path),
                "sha256": sha256(path),
                "fingerprints": [fingerprint(item) for item in candidates],
            }
        )
    eligible = []
    range_excluded_count = 0
    for raw_source in expansion.get("ranked_candidates") or []:
        raw = copy.deepcopy(dict(raw_source))
        if raw.get("boundary_corner") is True or raw.get("boundary_plateau") is True:
            continue
        if parameter_key(raw) in excluded_parameters:
            continue
        if resume_ranges is not None:
            parameters = raw.get("candidate_params") or {}
            if not isinstance(parameters, Mapping) or any(
                not (
                    finite(resume_ranges[name]["min_value"], f"range.{name}.min")
                    <= finite(parameters[name], f"candidate_params.{name}")
                    <= finite(resume_ranges[name]["max_value"], f"range.{name}.max")
                )
                for name in PARAMETER_BOUNDS
            ):
                range_excluded_count += 1
                continue
        predicted = raw.get("predicted_resource")
        if not isinstance(predicted, Mapping):
            raise RuntimeError("candidate lacks predicted_resource")
        derived_score, derived_utilization = selector.score_resource(predicted)
        raw.setdefault("candidate_score", derived_score)
        raw.setdefault("final_score", float(raw["candidate_score"]))
        raw.setdefault("predicted_score", float(raw["candidate_score"]))
        raw.setdefault("utilization", derived_utilization)
        missing = [name for name in REQUIRED_FIELDS if name not in raw]
        if missing:
            raise RuntimeError(f"candidate lacks audit fields: {missing}")
        eligible.append(raw)

    trusted = []
    probes = []
    for raw in eligible:
        regime = exact_regime(raw)
        if (
            regime in registry
            and registry[regime].get("trusted_prediction_eligible") is True
        ):
            calibrated = recalibrate(raw, registry[regime])
            if fingerprint(calibrated) in excluded_fingerprints:
                continue
            trusted.append(calibrated)
        else:
            if fingerprint(raw) in excluded_fingerprints:
                continue
            probe = (
                formal_incumbent_plan_probe(raw)
                if raw.get("incumbent_exact_plan_preserved") is True
                else hierarchical_calibrate_probe(raw, probe_prior)
            )
            probe.update(
                {
                    "predicted_score": float(probe["candidate_score"]),
                    "structural_plan_regime_id": regime,
                    "exact_plan_regime_validated": False,
                    "trusted": False,
                    "uncertain_probe": True,
                }
            )
            probe["exact_plan_regime_id"] = selector.exact_plan_regime_id(probe)
            probes.append(probe)
    trusted.sort(key=lambda item: float(item["candidate_score"]), reverse=True)
    probes.sort(key=probe_rank_score, reverse=True)
    all_calibrated_and_unknown = [*trusted, *probes]
    if not all_calibrated_and_unknown:
        raise RuntimeError("no eligible candidates remain for score ladder")
    observed_scores = [float(item["actual_score"]) for item in results]
    observed_center = float(statistics.median(observed_scores))
    observed_noise_floor = max(
        0.002,
        1.4826
        * float(
            statistics.median(
                abs(value - observed_center) for value in observed_scores
            )
        ),
    )
    credible_frontier = max(
        probe_rank_score(item)
        for item in all_calibrated_and_unknown
    )
    ladder_incumbent = (
        incumbent_score
        if incumbent_score is not None
        else observed_center
    )
    frontier_gain = max(0.0, credible_frontier - ladder_incumbent)
    target_gain = (
        min(frontier_gain, observed_noise_floor)
        if frontier_gain > 0.0
        else 0.002
    )
    target_gain = max(0.002, target_gain)
    target_score = ladder_incumbent + target_gain
    minimum_trusted_interval_gap = 0.002
    selected_trusted = stability_separated_trusted_take(
        trusted,
        3,
        minimum_trusted_interval_gap,
        target_score,
    )
    selected_probes, probe_ladder = score_ladder_probe_take(
        probes,
        2,
        target_score,
    )
    recovery_mode = False
    if len(selected_trusted) != 3 or len(selected_probes) != 2:
        raise RuntimeError(
            "fail closed: cannot form stable trusted3+credible-probe2; "
            "five-probe novelty recovery is forbidden: "
            f"eligible={len(eligible)} trusted={len(trusted)} "
            f"probes={len(probes)} stable_trusted="
            f"{len(selected_trusted)} credible_unknown_probes="
            f"{len(selected_probes)} minimum_trusted_interval_gap="
            f"{minimum_trusted_interval_gap} target_score={target_score:.12g}"
        )
    top5 = [*selected_trusted, *selected_probes]
    for index, item in enumerate(top5, 1):
        trusted_role = index <= 3
        item.update(
            {
                "rank": index,
                "selection_role": "trusted" if trusted_role else "probe",
                "role": "trusted" if trusted_role else "uncertain_probe",
                "trusted": trusted_role,
                "uncertain_probe": not trusted_role,
                "diagnostic_only": True,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "range_update_allowed": False,
                "gradient_update_allowed": False,
                "incumbent_update_eligible": False,
                "eligible_for_best_candidate": False,
                "qps_used_for_inner_scoring": False,
                "boundary_corner": False,
            }
        )
    identifiers = [fingerprint(item) for item in top5]
    if len(set(identifiers)) != 5:
        raise RuntimeError("selected Top-5 fingerprints are not unique")
    score_span = max(float(item["candidate_score"]) for item in top5) - min(
        float(item["candidate_score"]) for item in top5
    )
    acquisition_score_span = max(probe_rank_score(item) for item in top5) - min(
        probe_rank_score(item) for item in top5
    )
    minimum_score_span = finite(minimum_score_span, "minimum_score_span")
    if minimum_score_span < 0.0 or score_span <= minimum_score_span:
        raise RuntimeError(
            "selected Top-5 score span does not exceed replay noise: "
            f"span={score_span:.12g} bound={minimum_score_span:.12g}"
        )

    output = {
        "schema_version": "tpch_autonomous_successor_top5_v1",
        "selection_contract": (
            "exact_structural_plan_regime_trusted3_bounded_probe2_top5_"
            "diagnostic_v1"
        ),
        "diagnostic_only": True,
        "formal_continuation": False,
        "accepted_count_start": int(summary["accepted_count"]),
        "accepted_shrink_target": 10,
        "maximum_real_replay_invocations": 5,
        "serial_real_replay_required": True,
        "technical_failures_count_toward_physical_cap": True,
        "technical_failures_count_as_valid_or_accepted": False,
        "rejected_updates_forbidden": True,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "qps_used_for_inner_scoring": False,
        "unknown_plan_regime_trusted_allowed": False,
        "trusted_slots": 3,
        "probe_slots": 2,
        "automatic_recovery_mode": False,
        "automatic_recovery_reason": None,
        "unknown_ucb_bonus_cap": MAX_UNKNOWN_UCB_BONUS,
        "probe_expected_score_must_clear_target": True,
        "minimum_trusted_prediction_interval_gap": (
            minimum_trusted_interval_gap
        ),
        "novel_unique_fingerprint_count": 5,
        "minimum_required_score_span": minimum_score_span,
        "selected_candidate_score_span": score_span,
        "selected_probe_acquisition_score_span": acquisition_score_span,
        "score_span_gate_passed": True,
        "unknown_regime_hierarchical_probe_prior": copy.deepcopy(probe_prior),
        "score_ladder": {
            **probe_ladder,
            "accepted_count": 10 - remaining_accepts,
            "accepted_target": 10,
            "remaining_accepts": remaining_accepts,
            "incumbent_score": ladder_incumbent,
            "observed_replay_noise_floor": observed_noise_floor,
            "frontier_gain": frontier_gain,
            "target_gain": target_gain,
            "target_score": target_score,
            "manual_candidate_injection": False,
        },
        "validated_plan_regimes": registry,
        "previous_batch_feedback_audit": feedback,
        "source_evidence": {
            "selection": {
                "path": str(selection_path),
                "sha256": sha256(selection_path),
            },
            "summary": {
                "path": str(summary_path),
                "sha256": sha256(summary_path),
            },
            "expansion": {
                "path": str(expansion_path),
                "sha256": sha256(expansion_path),
            },
            "additional_exclusions": additional_exclusions,
            "resume_state": (
                {
                    "path": str(resume_state_path),
                    "sha256": sha256(resume_state_path),
                    "accepted_count": int(resume_state["accepted_count"]),
                    "range_state": copy.deepcopy(dict(resume_ranges)),
                }
                if resume_state_path is not None
                and resume_state is not None
                and resume_ranges is not None
                else None
            ),
        },
        "selection_audit": {
            "eligible_novel_noncorner_count": len(eligible),
            "eligible_trusted_candidate_count": len(trusted),
            "eligible_unknown_candidate_count": len(probes),
            "selected_unique_fingerprint_count": len(set(identifiers)),
            "trusted_count": 3,
            "probe_count": 2,
            "range_excluded_candidate_count": range_excluded_count,
        },
        "top5": top5,
        "ranked_candidates": top5,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous-selection", type=Path, required=True)
    parser.add_argument("--previous-summary", type=Path, required=True)
    parser.add_argument("--expanded-candidates", type=Path, required=True)
    parser.add_argument(
        "--additional-exclusion-selection",
        action="append",
        default=[],
        type=Path,
        help="exclude every fingerprint and parameter point from another Top-5",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-score-span", type=float, default=0.0)
    parser.add_argument(
        "--resume-state",
        type=Path,
        help="clean prior formal state that supplies incumbent-shrunk ranges",
    )
    args = parser.parse_args()
    output = build(
        args.previous_selection.resolve(),
        args.previous_summary.resolve(),
        args.expanded_candidates.resolve(),
        args.output.resolve(),
        tuple(path.resolve() for path in args.additional_exclusion_selection),
        args.minimum_score_span,
        args.resume_state.resolve() if args.resume_state else None,
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "selection_sha256": sha256(args.output.resolve()),
                "roles": [item["selection_role"] for item in output["top5"]],
                "new_workload_calls": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
