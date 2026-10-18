#!/usr/bin/env python3
"""Build a TPC-C Top-5 with robust exact-plan-regime calibration."""

from __future__ import annotations

import copy
import itertools
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_round13_plan_regime_corrected_top5 as common


ATTEMPT = common.ATTEMPT
DISCOVERY_ROOT = (
    ATTEMPT / "gradient_debug_noncorner_regime_discovery_v2_20260729T1702"
)
CORRECTED_ROOT = (
    ATTEMPT / "gradient_debug_round13_exact_plan_regime_corrected_v2_20260729T1742"
)
SOURCES = (
    (
        DISCOVERY_ROOT / "top5.json",
        DISCOVERY_ROOT / "strict_top5_real_replay_round12_ledger_v1.json",
        "discovery",
    ),
    (
        CORRECTED_ROOT / "top5.json",
        CORRECTED_ROOT / "strict_top5_real_replay_round13_ledger_v1.json",
        "corrected",
    ),
)
OUTPUT = Path("/tmp/tpcc_round14_robust_regime_top5_v3.json")
SINGLE_OBSERVATION_RELATIVE_HALF_WIDTH = 0.20
# A plan regime remains calibratable across a resource-scale epoch shift when
# its repeated measurements stay within a factor of two.  The previous 0.25
# threshold incorrectly exhausted every trusted plan after the current I/O
# baseline moved coherently.  Entry still fails closed on any order-of-
# magnitude error, while the newest observation is selected by the even-sample
# upper median below and is independently checked by the formal batch.
MAX_TRUSTED_RELATIVE_SPAN = 1.0
# Resource-scale calibration and ranking calibration are intentionally
# different gates.  A factor-of-two regime can satisfy the experiment's
# order-of-magnitude safety check while still being far too noisy to rank
# nearby candidates.  Only a local (or repeated exact-point) prediction with
# at most 25% relative spread may be labelled trusted.
MAX_RANKING_RELATIVE_SPAN = 0.25
MAX_TRUSTED_KNN_DISTANCE = 0.35
MIN_EXACT_POINT_OBSERVATIONS = 2
# The selected trusted Top-3 must expose a signal larger than the replay-noise
# floor used by diagnostic acquisition.  The autonomous wrapper can raise
# this value from its sealed evidence manifest.
MIN_TRUSTED_PREDICTED_SCORE_SPAN = 0.0
PROBES_MUST_BE_UNKNOWN = False
PARAM_RANGES_OVERRIDE: Path | None = None
FORMAL_STATE_AUDIT_SOURCE: Path | None = None
TRUSTED_PLAN_DIVERSITY_REQUIRED = True
ALLOW_SPARSE_EXACT_REGIME_PROBES = False
SPARSE_EXACT_REGIME_MAX_OBSERVATIONS = 1
CANDIDATE_RANGE_POLICY = "configured_generation_range_v1"
DIAGNOSTIC_PROBE_ONLY = False
ALLOW_VALIDATED_ANCHOR_REPLAY = False
FORMAL_EXPECTED_IMPROVEMENT_REQUIRED = False
MIN_PREDICTED_IMPROVEMENT = 1e-6
MINIMUM_DIAGNOSTIC_SCORE_SPAN = 0.0
EXTRA_EXCLUDED_FINGERPRINTS: set[str] = set()
MAX_RELATIVE_STEP_FROM_FORMAL_INCUMBENT = 0.80
ADAPTIVE_GRADIENT_ETA_LEVELS = (0.05, 0.10, 0.20, 0.40, 0.80)
ACCEPTED_TARGET = 10
SCORE_LADDER_SAFETY_MARGIN = 2
SCORE_LADDER_MIN_GAIN = 1e-4
SAFE_FRONTIER_MAX_UTILIZATION = {
    "cpu": 0.95,
    "io": 0.90,
    "memory": 0.90,
}
RESOURCES = common.RESOURCES


def compute_score_ladder(
    incumbent_score: float,
    accepted_count: int,
    credible_frontier_score: float,
    observed_noise_floor: float,
) -> Dict[str, float | int | str | bool]:
    """compute score ladder."""
    values = (
        incumbent_score,
        credible_frontier_score,
        observed_noise_floor,
    )
    if (
        any(not math.isfinite(float(value)) for value in values)
        or not 0 <= accepted_count < ACCEPTED_TARGET
        or observed_noise_floor < 0.0
    ):
        raise ValueError("invalid score-ladder authority")
    remaining_accepts = max(1, ACCEPTED_TARGET - accepted_count)
    frontier_gain = max(0.0, credible_frontier_score - incumbent_score)
    noise_guard = max(SCORE_LADDER_MIN_GAIN, observed_noise_floor / 3.0)
    target_gain = min(
        frontier_gain,
        max(
            noise_guard,
            frontier_gain
            / (remaining_accepts + SCORE_LADDER_SAFETY_MARGIN),
        ),
    )
    return {
        "contract": "remaining_accepts_credible_frontier_ladder_v1",
        "accepted_count": accepted_count,
        "accepted_target": ACCEPTED_TARGET,
        "remaining_accepts": remaining_accepts,
        "safety_margin": SCORE_LADDER_SAFETY_MARGIN,
        "incumbent_score": incumbent_score,
        "credible_frontier_score": credible_frontier_score,
        "frontier_gain": frontier_gain,
        "noise_guard": noise_guard,
        "target_gain": target_gain,
        "target_score": incumbent_score + target_gain,
        "manual_candidate_injection": False,
    }


def conservative_score_interval(
    resource_interval: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Evaluate every score breakpoint inside a rectangular resource interval.

    The directional band-target utility is piecewise quadratic/linear.  Its
    extrema can occur at an interval endpoint or at a target-band breakpoint,
    so evaluating only the median prediction is unsafe for formal admission.
    """
    values_by_resource: Dict[str, List[float]] = {}
    for name in RESOURCES:
        bounds = resource_interval[name]
        lower = float(bounds["lower"])
        median = float(bounds["median"])
        upper = float(bounds["upper"])
        if not 0.0 <= lower <= median <= upper:
            raise RuntimeError(f"invalid predicted resource interval: {name}")
        values = {lower, median, upper}
        target = common.SCORE.resource_target_ranges[
            common.SCORE._normalize_resource_name(name)
        ]
        for key in ("min", "max", "hard"):
            breakpoint = float(target[key]) * float(common.CAPABILITY[name])
            if lower <= breakpoint <= upper:
                values.add(breakpoint)
        values_by_resource[name] = sorted(values)

    evaluated = []
    for combination in itertools.product(
        *(values_by_resource[name] for name in RESOURCES)
    ):
        resource = {
            name: float(combination[index])
            for index, name in enumerate(RESOURCES)
        }
        evaluated.append((common.score(resource), resource))
    if not evaluated:
        raise RuntimeError("score interval evaluation produced no points")
    lower_score, lower_resource = min(evaluated, key=lambda item: item[0])
    upper_score, upper_resource = max(evaluated, key=lambda item: item[0])
    median_resource = {
        name: float(resource_interval[name]["median"]) for name in RESOURCES
    }
    return {
        "lower": float(lower_score),
        "median": common.score(median_resource),
        "upper": float(upper_score),
        "lower_witness_resource": lower_resource,
        "upper_witness_resource": upper_resource,
        "evaluated_point_count": len(evaluated),
        "method": "resource_box_band_breakpoints_v1",
    }


def load_clean_batch(
    selection_path: Path,
    ledger_path: Path,
    source_name: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    selected = sorted(selection["top5"], key=lambda item: int(item["rank"]))
    successful = sorted(ledger.get("successful", []), key=lambda item: int(item["rank"]))
    expected_count = int(
        selection.get("maximum_real_workload_invocations") or len(selected)
    )
    if (
        expected_count <= 0
        or ledger.get("real_workload_invocation_count") != expected_count
        or ledger.get("technical_failures") != []
        or len(selected) != expected_count
        or len(successful) != expected_count
    ):
        raise RuntimeError(
            f"{source_name} is not a clean {expected_count}/{expected_count} "
            "diagnostic batch"
        )
    by_rank = {int(item["rank"]): item for item in selected}
    for result in successful:
        rank = int(result["rank"])
        candidate = by_rank[rank]
        protocol = result.get("measurement_protocol") or {}
        if (
            protocol.get("correctness_passed") is not True
            or protocol.get("stable") is not True
            or protocol.get("technical_failure") is not False
            or result.get("diagnostic_only") is not True
            or result.get("eligible_for_incumbent_or_accepted") is not False
            or result["workload_plan_fingerprint"]
            != candidate["workload_plan_fingerprint"]
            or result["three_level_fingerprint"]["id"]
            != candidate["three_level_fingerprint"]["id"]
        ):
            raise RuntimeError(f"{source_name} rank {rank} failed validation")
        summary_path = Path(protocol["validated_summary_path"])
        if common.sha256(summary_path) != protocol["validated_summary_sha256"]:
            raise RuntimeError(f"{source_name} rank {rank} summary SHA mismatch")
    return selected, successful


def robust_regimes(
    batches: List[Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]],
) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, List[Dict[str, Any]]] = {}
    for selected, successful, source_name in batches:
        by_rank = {int(item["rank"]): item for item in selected}
        for result in successful:
            candidate = by_rank[int(result["rank"])]
            plan = result["workload_plan_fingerprint"]
            uncalibrated = candidate.get("uncalibrated_predicted_resource")
            if not isinstance(uncalibrated, dict):
                uncalibrated = candidate["predicted_resource"]
            scalar = {
                name: float(result["actual_resource"][name])
                / float(uncalibrated[name])
                for name in RESOURCES
            }
            if any(not 0.1 <= value <= 10.0 for value in scalar.values()):
                raise RuntimeError(f"resource magnitude failure for {plan}")
            rows.setdefault(plan, []).append(
                {
                    "source": source_name,
                    "rank": int(result["rank"]),
                    "actual_resource": copy.deepcopy(result["actual_resource"]),
                    "actual_score": float(result["actual_score"]),
                    "candidate_params": copy.deepcopy(
                        candidate["optimized_params"]
                    ),
                    "three_level_fingerprint_id": candidate[
                        "three_level_fingerprint"
                    ]["id"],
                    "uncalibrated_predicted_resource": copy.deepcopy(uncalibrated),
                    "resource_scalar": scalar,
                    "summary_path": result["measurement_protocol"][
                        "validated_summary_path"
                    ],
                    "summary_sha256": result["measurement_protocol"][
                        "validated_summary_sha256"
                    ],
                }
            )

    regimes: Dict[str, Dict[str, Any]] = {}
    for plan, evidence in rows.items():
        count = len(evidence)
        scalar = {
            name: statistics.median(
                float(item["resource_scalar"][name]) for item in evidence
            )
            for name in RESOURCES
        }
        actual_median = {
            name: statistics.median(
                float(item["actual_resource"][name]) for item in evidence
            )
            for name in RESOURCES
        }
        relative_span = {}
        for name in RESOURCES:
            values = [float(item["actual_resource"][name]) for item in evidence]
            if count == 1:
                relative_span[name] = SINGLE_OBSERVATION_RELATIVE_HALF_WIDTH
            else:
                relative_span[name] = (max(values) - min(values)) / actual_median[name]
        # One clean replay validates the exact plan identity, but it cannot
        # establish regime stability.  Keep single-observation regimes probe
        # only until a second clean replay confirms the resource scale.
        stable = (
            count >= 2
            and max(relative_span.values()) <= MAX_TRUSTED_RELATIVE_SPAN
        )
        evidence_by_params: Dict[str, List[Dict[str, Any]]] = {}
        for item in evidence:
            params_id = common.canonical_sha256(
                {
                    name: float(value)
                    for name, value in sorted(
                        item["candidate_params"].items()
                    )
                }
            )
            evidence_by_params.setdefault(params_id, []).append(item)
        exact_point_repeats = []
        for params_id, point_evidence in evidence_by_params.items():
            if len(point_evidence) < MIN_EXACT_POINT_OBSERVATIONS:
                continue
            score_values = [
                float(item["actual_score"]) for item in point_evidence
            ]
            resource_relative_span = {}
            for name in RESOURCES:
                values = [
                    float(item["actual_resource"][name])
                    for item in point_evidence
                ]
                median = statistics.median(values)
                resource_relative_span[name] = (
                    (max(values) - min(values)) / median
                    if median > 0.0
                    else 1.0
                )
            exact_point_repeats.append(
                {
                    "parameter_key": params_id,
                    "observation_count": len(point_evidence),
                    "actual_score_span": max(score_values) - min(score_values),
                    "relative_resource_span": resource_relative_span,
                    "ranking_stable": bool(
                        max(resource_relative_span.values())
                        <= MAX_RANKING_RELATIVE_SPAN
                    ),
                }
            )
        regimes[plan] = {
            "plan": plan,
            "observation_count": count,
            "resource_scalar_median": scalar,
            "actual_resource_median": actual_median,
            "actual_score_median": statistics.median(
                float(item["actual_score"]) for item in evidence
            ),
            "actual_score_span": (
                max(float(item["actual_score"]) for item in evidence)
                - min(float(item["actual_score"]) for item in evidence)
            ),
            "relative_resource_span": relative_span,
            "trusted_eligible": stable,
            "exact_point_repeats": exact_point_repeats,
            "evidence": evidence,
        }
    return regimes


def parameter_key(params: Mapping[str, Any]) -> str:
    return common.canonical_sha256(
        {name: float(value) for name, value in sorted(params.items())}
    )


def radical_inverse(index: int, base: int) -> float:
    """Return one deterministic Halton coordinate in the open unit interval."""
    if index <= 0 or base <= 1:
        raise ValueError("radical inverse requires index > 0 and base > 1")
    value = 0.0
    factor = 1.0 / float(base)
    remaining = index
    while remaining:
        remaining, digit = divmod(remaining, base)
        value += float(digit) * factor
        factor /= float(base)
    return value


def adaptive_gradient_boundary_points(
    anchor_params: Mapping[str, Any],
    gradient: Mapping[str, Any],
    ranges: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Generate deterministic geometric-eta points along the sealed gradient."""
    names = sorted(ranges)
    if (
        not names
        or set(names) != set(anchor_params)
        or set(names) - set(gradient)
    ):
        raise RuntimeError("adaptive gradient authority is incomplete")
    absolute_gradient = {
        name: abs(float(gradient[name])) for name in names
    }
    maximum_gradient = max(absolute_gradient.values())
    if not math.isfinite(maximum_gradient) or maximum_gradient <= 0.0:
        raise RuntimeError("adaptive gradient has no finite direction")
    normalized_direction = {
        name: -float(gradient[name]) / maximum_gradient for name in names
    }
    for name in names:
        value = float(anchor_params[name])
        minimum = float(ranges[name]["min_value"])
        maximum = float(ranges[name]["max_value"])
        if not minimum < value < maximum:
            raise RuntimeError(
                f"adaptive anchor is outside strict safe envelope: {name}"
            )

    rows: List[Dict[str, Any]] = []

    def add_path(
        path_name: str,
        active_names: Tuple[str, ...],
        eta: float,
    ) -> None:
        params = {name: float(anchor_params[name]) for name in names}
        for name in active_names:
            direction = normalized_direction[name]
            if direction == 0.0:
                continue
            minimum = float(ranges[name]["min_value"])
            maximum = float(ranges[name]["max_value"])
            boundary = maximum if direction > 0.0 else minimum
            params[name] += eta * (boundary - params[name])
        rows.append(
            {
                "params": params,
                "eta": eta,
                "path": path_name,
                "active_parameters": list(active_names),
                "gradient_direction": copy.deepcopy(normalized_direction),
                "contract": (
                    "sealed_gradient_geometric_eta_boundary_expansion_v1"
                ),
            }
        )

    directional_names = tuple(
        name for name in names if normalized_direction[name] != 0.0
    )
    for eta in ADAPTIVE_GRADIENT_ETA_LEVELS:
        add_path("joint_original_gradient", directional_names, eta)
    for name in directional_names:
        for eta in ADAPTIVE_GRADIENT_ETA_LEVELS:
            add_path(f"coordinate_{name}", (name,), eta)
    strongest = sorted(
        directional_names,
        key=lambda name: (-absolute_gradient[name], name),
    )[:4]
    for left_index, left in enumerate(strongest):
        for right in strongest[left_index + 1 :]:
            for eta in ADAPTIVE_GRADIENT_ETA_LEVELS:
                add_path(f"paired_{left}_{right}", (left, right), eta)

    unique: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        identifier = parameter_key(row["params"])
        if identifier in seen:
            continue
        seen.add(identifier)
        unique.append(row)
    return unique


def require_five_diagnostic_probes(
    probes: List[Dict[str, Any]],
) -> None:
    """Fail closed unless acquisition produced five distinct fingerprints."""
    identifiers = [
        str((probe.get("three_level_fingerprint") or {}).get("id") or "")
        for probe in probes
    ]
    if (
        len(probes) != 5
        or len(set(identifiers)) != 5
        or any(
            not identifier.startswith("three-level-sha256:")
            for identifier in identifiers
        )
    ):
        available = len(
            set(identifier for identifier in identifiers if identifier)
        )
        raise RuntimeError(
            "insufficient novel diagnostic probe pool: "
            f"available={available}"
        )


def normalized_parameter_distance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    ranges: Mapping[str, Mapping[str, Any]],
) -> float:
    squared = 0.0
    for name, bounds in ranges.items():
        span = float(bounds["max_value"]) - float(bounds["min_value"])
        if span <= 0.0:
            continue
        delta = (float(left[name]) - float(right[name])) / span
        squared += delta * delta
    return math.sqrt(squared)


def parameter_knn_prediction(
    regime: Mapping[str, Any],
    params: Mapping[str, Any],
    ranges: Mapping[str, Mapping[str, Any]],
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """Predict candidate resources from nearby real points in one exact plan."""
    evidence = list(regime.get("evidence") or [])
    if len(evidence) < 2:
        raise ValueError("candidate-level KNN requires two real observations")
    neighbors = sorted(
        (
            (
                normalized_parameter_distance(
                    params,
                    item["candidate_params"],
                    ranges,
                ),
                item,
            )
            for item in evidence
        ),
        key=lambda pair: (
            pair[0],
            str(pair[1]["three_level_fingerprint_id"]),
        ),
    )
    if neighbors[0][0] <= 1e-12:
        exact = [
            item
            for distance, item in neighbors
            if distance <= 1e-12
        ]
        predicted = {
            name: statistics.median(
                float(item["actual_resource"][name]) for item in exact
            )
            for name in RESOURCES
        }
        relative_span = {}
        for name in RESOURCES:
            values = [
                float(item["actual_resource"][name]) for item in exact
            ]
            median = statistics.median(values)
            relative_span[name] = (
                (max(values) - min(values)) / median
                if len(values) >= MIN_EXACT_POINT_OBSERVATIONS and median > 0.0
                else SINGLE_OBSERVATION_RELATIVE_HALF_WIDTH
            )
        return predicted, {
            "contract": "exact_plan_parameter_knn_resource_v1",
            "neighbor_count": len(exact),
            "nearest_normalized_distance": 0.0,
            "maximum_neighbor_distance": 0.0,
            "weights": [1.0 / len(exact)] * len(exact),
            "relative_resource_span": relative_span,
            "exact_observation_match": True,
            "exact_observation_count": len(exact),
            "exact_repeat_stable": bool(
                len(exact) >= MIN_EXACT_POINT_OBSERVATIONS
                and max(relative_span.values()) <= MAX_RANKING_RELATIVE_SPAN
            ),
        }
    selected = neighbors[: min(7, len(neighbors))]
    # The 0.02 floor avoids a single near-duplicate becoming an unstable
    # extrapolator while still strongly favoring local empirical evidence.
    raw_weights = [1.0 / ((distance + 0.02) ** 2) for distance, _ in selected]
    total_weight = sum(raw_weights)
    weights = [value / total_weight for value in raw_weights]
    predicted = {
        name: sum(
            weight * float(item["actual_resource"][name])
            for weight, (_distance, item) in zip(weights, selected)
        )
        for name in RESOURCES
    }
    relative_span = {}
    for name in RESOURCES:
        values = [
            float(item["actual_resource"][name])
            for _distance, item in selected
        ]
        median = statistics.median(values)
        relative_span[name] = (
            (max(values) - min(values)) / median
            if median > 0.0
            else 1.0
        )
    return predicted, {
        "contract": "exact_plan_parameter_knn_resource_v1",
        "neighbor_count": len(selected),
        "nearest_normalized_distance": float(selected[0][0]),
        "maximum_neighbor_distance": float(selected[-1][0]),
        "weights": weights,
        "relative_resource_span": relative_span,
        "exact_observation_match": False,
    }


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    batches = []
    historical_ids = set()
    historical_params = set()
    anchors: List[Dict[str, Any]] = []
    source_manifest = []
    for selection_path, ledger_path, source_name in SOURCES:
        selected, successful = load_clean_batch(
            selection_path, ledger_path, source_name
        )
        batches.append((selected, successful, source_name))
        anchors.extend(selected)
        historical_ids.update(
            item["three_level_fingerprint"]["id"] for item in selected
        )
        historical_params.update(parameter_key(item["optimized_params"]) for item in selected)
        source_manifest.append(
            {
                "name": source_name,
                "selection_path": str(selection_path),
                "selection_sha256": common.sha256(selection_path),
                "ledger_path": str(ledger_path),
                "ledger_sha256": common.sha256(ledger_path),
            }
        )
    regimes = robust_regimes(batches)
    repeated_score_spans = [
        float(point["actual_score_span"])
        for regime in regimes.values()
        for point in regime.get("exact_point_repeats") or []
        if point.get("ranking_stable") is True
    ]
    observed_replay_noise_floor = (
        statistics.median(repeated_score_spans)
        if repeated_score_spans
        else 0.0
    )
    effective_min_trusted_score_span = max(
        MIN_TRUSTED_PREDICTED_SCORE_SPAN,
        observed_replay_noise_floor,
    )

    config = json.loads(common.CONFIG.read_text(encoding="utf-8"))
    range_override_sha256 = None
    formal_ranges_at_generation = None
    formal_incumbent_score = None
    formal_incumbent_params = None
    formal_accepted_count = None
    if FORMAL_STATE_AUDIT_SOURCE is not None:
        formal_state_snapshot = json.loads(
            FORMAL_STATE_AUDIT_SOURCE.resolve().read_text(encoding="utf-8")
        )
        formal_ranges_at_generation = copy.deepcopy(
            formal_state_snapshot["range_state"]
        )
        formal_incumbent_score = float(
            formal_state_snapshot["incumbent_score"]
        )
        formal_accepted_count = int(
            formal_state_snapshot["accepted_count"]
        )
        formal_incumbent_params = {
            name: float(value)
            for name, value in formal_state_snapshot[
                "incumbent_params"
            ].items()
        }
        if (
            not math.isfinite(formal_incumbent_score)
            or not 0 <= formal_accepted_count < ACCEPTED_TARGET
            or set(formal_incumbent_params) != set(config["param_ranges"])
        ):
            raise RuntimeError("formal incumbent state is invalid")
    if PARAM_RANGES_OVERRIDE is not None:
        range_override_path = PARAM_RANGES_OVERRIDE.resolve()
        range_override_state = json.loads(
            range_override_path.read_text(encoding="utf-8")
        )
        override = range_override_state.get("range_state")
        if (
            not isinstance(override, dict)
            or set(override) != set(config["param_ranges"])
            or any(
                not float(bounds["min_value"]) < float(bounds["max_value"])
                for bounds in override.values()
            )
        ):
            raise RuntimeError("formal parameter-range override is invalid")
        config["param_ranges"] = copy.deepcopy(override)
        range_override_sha256 = common.sha256(range_override_path)
    if formal_incumbent_params is not None:
        invariant_violations = {
            name: {
                "incumbent": float(formal_incumbent_params[name]),
                "min_value": float(bounds["min_value"]),
                "max_value": float(bounds["max_value"]),
            }
            for name, bounds in config["param_ranges"].items()
            if not (
                float(bounds["min_value"])
                <= float(formal_incumbent_params[name])
                <= float(bounds["max_value"])
            )
        }
        if invariant_violations:
            raise RuntimeError(
                "formal incumbent is outside the active range; "
                "state migration is required before candidate generation: "
                f"{invariant_violations}"
            )
    ranking = common.load_ranking()
    raw_candidates = list(ranking["candidate_rankings"])
    source_anchor = raw_candidates[0]
    baseline_params = {
        name: float(value) for name, value in source_anchor["initial_params"].items()
    }
    baseline_real = source_anchor["initial_resource"]
    queries = common.query_set(source_anchor)
    Estimator = common.load_estimator_class()
    estimator = Estimator(
        pg_host="127.0.0.1",
        pg_port="59640",
        pg_user="postgres",
        pg_dbname="fullgrid_tpcc",
        estimator_cfg=copy.deepcopy(config["metrics"]["plan_estimator"]),
        plan_capture_modes=["generic"],
        verbose=False,
    )
    estimator.calibrate(queries, baseline_params, baseline_real)

    proposals: List[
        Tuple[
            Dict[str, float],
            Dict[str, Any],
            str,
            bool,
            Dict[str, Any] | None,
        ]
    ] = []
    for source in raw_candidates:
        proposals.append(
            (
                common.clipped_non_corner(
                    source["optimized_params"], config["param_ranges"]
                ),
                source,
                f"raw_rank_{source['rank']}",
                False,
                None,
            )
        )
    # Explore one representative anchor per exact plan regime.  Replaying
    # every historical anchor multiplies identical EXPLAIN neighborhoods and
    # made autonomous recovery take tens of minutes after the evidence ledger
    # grew.  Prefer the six highest-scoring stable regimes for trusted
    # acquisition and retain six non-stable regimes for probe discovery.
    anchor_by_plan: Dict[str, Dict[str, Any]] = {}
    for anchor in anchors:
        plan = str(anchor.get("workload_plan_fingerprint") or "")
        if not plan:
            continue
        incumbent = anchor_by_plan.get(plan)
        if incumbent is None or float(anchor.get("candidate_score", 0.0)) > float(
            incumbent.get("candidate_score", 0.0)
        ):
            anchor_by_plan[plan] = anchor
    stable_plans = sorted(
        (
            plan
            for plan, regime in regimes.items()
            if regime["trusted_eligible"] and plan in anchor_by_plan
        ),
        key=lambda plan: (
            -float(regimes[plan]["actual_score_median"]),
            plan,
        ),
    )[:6]
    probe_plans = sorted(
        (
            plan
            for plan, regime in regimes.items()
            if not regime["trusted_eligible"] and plan in anchor_by_plan
        ),
        key=lambda plan: (
            -float(regimes[plan]["actual_score_median"]),
            plan,
        ),
    )[:6]
    exploration_anchors = [
        (anchor_by_plan[plan], True) for plan in stable_plans
    ] + [
        (anchor_by_plan[plan], False) for plan in probe_plans
    ]
    for anchor, stable_anchor in exploration_anchors:
        if ALLOW_VALIDATED_ANCHOR_REPLAY:
            proposals.append(
                (
                    common.clipped_non_corner(
                        anchor["optimized_params"],
                        config["param_ranges"],
                    ),
                    anchor,
                    (
                        "validated_trusted_anchor_replay"
                        if stable_anchor
                        else "validated_probe_anchor_replay"
                    ),
                    True,
                    None,
                )
            )
        for name, bounds in config["param_ranges"].items():
            span = float(bounds["max_value"]) - float(bounds["min_value"])
            # Coarse steps frequently cross a plan boundary and eventually
            # exhaust every novel candidate in otherwise stable regimes.
            # Add local steps so EXPLAIN can discover new three-level
            # fingerprints that remain inside an already validated exact
            # regime.  Fingerprints are still computed from the fresh plan
            # capture below; no regime identity is inferred from proximity.
            # Retain the local steps needed to stay inside a trusted regime,
            # then expand geometrically until EXPLAIN crosses into distinct
            # regimes for uncertainty probes.  The previous local-only
            # schedule could exhaust the second probe after several formal
            # batches even though unexplored non-corner regimes remained.
            absolute_fractions = (
                (0.00025, 0.001, 0.005, 0.02, 0.08, 0.25)
                if stable_anchor
                else (0.005, 0.02, 0.08, 0.25)
            )
            for level, absolute_fraction in enumerate(
                absolute_fractions, 1
            ):
                for direction in (-1, 1):
                    fraction = direction * absolute_fraction
                    params = {
                        key: float(value)
                        for key, value in anchor["optimized_params"].items()
                    }
                    params[name] += span * fraction
                    proposals.append(
                        (
                            common.clipped_non_corner(
                                params, config["param_ranges"]
                            ),
                            anchor,
                            (
                                f"staircase_anchor_{anchor['rank']}_{name}_"
                                f"{direction:+d}_level_{level:02d}_"
                                f"{fraction:+.5f}"
                            ),
                            False,
                            {
                                "contract": (
                                    "within_exact_plan_geometric_staircase_v1"
                                ),
                                "anchor_plan": anchor.get(
                                    "workload_plan_fingerprint"
                                ),
                                "parameter": name,
                                "direction": direction,
                                "level": level,
                                "relative_step": fraction,
                            },
                        )
                    )

    if DIAGNOSTIC_PROBE_ONLY:
        # Repeated autonomous acquisitions eventually consume every
        # one-parameter neighbor even though most of the joint parameter
        # interior remains unexplored.  Add a deterministic, low-discrepancy
        # multi-parameter pool.  These points are diagnostic probes only:
        # fresh EXPLAIN still determines the plan and three-level fingerprint,
        # and the normal novelty/non-corner gates below remain authoritative.
        parameter_names = sorted(config["param_ranges"])
        primes = (2, 3, 5, 7, 11, 13, 17)
        if len(parameter_names) > len(primes):
            raise RuntimeError("Halton diagnostic pool lacks parameter bases")
        # Sample directly inside the safe acquisition envelope.  Sampling the
        # full seven-dimensional formal box and only later applying the 80%
        # incumbent-relative cap makes the feasible volume vanishingly small;
        # after several batches that left only four novel fingerprints.  The
        # intersection below is the exact domain already enforced later, with
        # a strict interior margin.  It increases coverage without widening
        # any bound or mutating formal range state.
        diagnostic_ranges: Dict[str, Dict[str, float]] = {}
        for name in parameter_names:
            bounds = config["param_ranges"][name]
            minimum = float(bounds["min_value"])
            maximum = float(bounds["max_value"])
            if formal_incumbent_params is not None:
                incumbent = float(formal_incumbent_params[name])
                radius = MAX_RELATIVE_STEP_FROM_FORMAL_INCUMBENT * max(
                    abs(incumbent), 1e-12
                )
                minimum = max(minimum, incumbent - radius)
                maximum = min(maximum, incumbent + radius)
            if not minimum < maximum:
                raise RuntimeError(
                    f"empty diagnostic safe envelope: {name}"
                )
            diagnostic_ranges[name] = {
                "min_value": minimum,
                "max_value": maximum,
            }
        # Keep the EXPLAIN pool bounded.  Each candidate inherits a sealed
        # provenance record, so hundreds of simultaneously feasible joint
        # points cause unnecessary deep-copy memory growth.  Forty-eight
        # low-discrepancy points plus the bounded joint directions below give
        # ample maximin choice for five probes while remaining reproducible.
        for sequence_index in range(1, 49):
            params = {}
            for offset, name in enumerate(parameter_names):
                bounds = diagnostic_ranges[name]
                minimum = float(bounds["min_value"])
                maximum = float(bounds["max_value"])
                span = maximum - minimum
                if span <= 0.0:
                    raise RuntimeError(
                        f"invalid diagnostic parameter range: {name}"
                    )
                coordinate = radical_inverse(
                    sequence_index + len(historical_params),
                    primes[offset],
                )
                # Keep a strict 2% margin so no generated point can become a
                # parameter-space corner through floating-point rounding.
                params[name] = minimum + span * (
                    0.02 + 0.96 * coordinate
                )
            proposals.append(
                (
                    common.clipped_non_corner(
                        params,
                        config["param_ranges"],
                    ),
                    source_anchor,
                    (
                        "diagnostic_halton_joint_interior_"
                        f"{sequence_index:04d}"
                    ),
                    False,
                    None,
                )
            )
        # Halton points cover the interior well, but planner transitions often
        # require several cost ratios to move together.  Enumerate the 2^7
        # near-vertex combinations with a strict 1% boundary margin.  These
        # are not corners and are still subject to fresh EXPLAIN and novelty
        # checks; they simply expose correlated join/scan/parallel regimes
        # that one-coordinate and central low-discrepancy probes can miss.
        joint_masks = range(0, 1 << len(parameter_names))
        for mask in joint_masks:
            params = {}
            for offset, name in enumerate(parameter_names):
                bounds = diagnostic_ranges[name]
                minimum = float(bounds["min_value"])
                maximum = float(bounds["max_value"])
                coordinate = 0.99 if mask & (1 << offset) else 0.01
                params[name] = minimum + (
                    maximum - minimum
                ) * coordinate
            proposals.append(
                (
                    common.clipped_non_corner(
                        params,
                        config["param_ranges"],
                    ),
                    source_anchor,
                    f"diagnostic_joint_near_vertex_{mask:03d}",
                    False,
                    None,
                )
            )

    candidates: List[Dict[str, Any]] = []
    seen_params = set(historical_params)
    seen_ids = set(historical_ids)
    crossed_staircases = set()
    adaptive_expansion_audit: Dict[str, Any] = {
        "contract": "sealed_gradient_geometric_eta_boundary_expansion_v1",
        "attempted": False,
        "candidate_count_before": None,
        "proposal_count": 0,
        "eta_levels": list(ADAPTIVE_GRADIENT_ETA_LEVELS),
        "manual_candidate_injection": False,
    }
    proposal_index = 0
    while True:
        if proposal_index >= len(proposals):
            if (
                DIAGNOSTIC_PROBE_ONLY
                and adaptive_expansion_audit["attempted"] is False
                and len(candidates) < 5
            ):
                adaptive_expansion_audit["attempted"] = True
                adaptive_expansion_audit["candidate_count_before"] = len(
                    candidates
                )
                if formal_incumbent_params is None:
                    raise RuntimeError(
                        "adaptive diagnostic expansion lacks formal incumbent"
                    )
                adaptive_rows = adaptive_gradient_boundary_points(
                    formal_incumbent_params,
                    source_anchor["g_j"],
                    diagnostic_ranges,
                )
                adaptive_expansion_audit["proposal_count"] = len(
                    adaptive_rows
                )
                for adaptive_index, row in enumerate(adaptive_rows, 1):
                    adaptive_source = copy.deepcopy(source_anchor)
                    adaptive_source["eta"] = float(row["eta"])
                    adaptive_source["adaptive_gradient_expansion"] = {
                        key: copy.deepcopy(value)
                        for key, value in row.items()
                        if key != "params"
                    }
                    proposals.append(
                        (
                            common.clipped_non_corner(
                                row["params"],
                                config["param_ranges"],
                            ),
                            adaptive_source,
                            (
                                "diagnostic_adaptive_gradient_"
                                f"{adaptive_index:04d}_{row['path']}_"
                                f"eta_{float(row['eta']):.5f}"
                            ),
                            False,
                            None,
                        )
                    )
                if not adaptive_rows:
                    raise RuntimeError(
                        "adaptive diagnostic expansion produced no proposals"
                    )
                continue
            break
        params, source, provenance, allow_seen, staircase = proposals[
            proposal_index
        ]
        proposal_index += 1
        params_id = parameter_key(params)
        if params_id in seen_params and not allow_seen:
            continue
        seen_params.add(params_id)
        formal_relative_step = None
        if formal_incumbent_params is not None:
            formal_relative_step = {
                name: (
                    float(params[name]) - float(formal_incumbent_params[name])
                )
                / max(abs(float(formal_incumbent_params[name])), 1e-12)
                for name in config["param_ranges"]
            }
            if max(abs(value) for value in formal_relative_step.values()) > (
                MAX_RELATIVE_STEP_FROM_FORMAL_INCUMBENT + 1e-12
            ):
                continue
            if formal_ranges_at_generation is not None and any(
                not (
                    float(formal_ranges_at_generation[name]["min_value"])
                    < float(params[name])
                    < float(formal_ranges_at_generation[name]["max_value"])
                )
                for name in config["param_ranges"]
            ):
                continue
        raw_resource, _stats, metadata = estimator.estimate_with_metadata(queries, params)
        plan = str(metadata.get("workload_plan_fingerprint") or "")
        if not plan.startswith("ratio-plan-sha256:"):
            raise RuntimeError(f"missing plan fingerprint: {provenance}")
        first_plan_change = False
        if staircase is not None:
            staircase_key = (
                str(staircase["anchor_plan"]),
                str(staircase["parameter"]),
                int(staircase["direction"]),
            )
            if staircase_key in crossed_staircases:
                continue
            if plan != staircase["anchor_plan"]:
                crossed_staircases.add(staircase_key)
                first_plan_change = True
        feature_ratio = {
            name: float(metadata["feature_ratios"][name]) for name in RESOURCES
        }
        regime = regimes.get(plan)
        regime_trusted = bool(regime and regime["trusted_eligible"])
        validated_replay_evidence = None
        matching_evidence: List[Dict[str, Any]] = []
        if allow_seen and regime is not None:
            matching_evidence = [
                item
                for item in regime["evidence"]
                if parameter_key(item["candidate_params"]) == params_id
            ]
            if matching_evidence:
                validated_replay_evidence = matching_evidence[-1]
        scalar = (
            regime["resource_scalar_median"]
            if regime is not None
            else {name: 1.0 for name in RESOURCES}
        )
        knn_audit = None
        if regime is not None and len(regime.get("evidence") or []) >= 2:
            predicted_resource, knn_audit = parameter_knn_prediction(
                regime,
                params,
                config["param_ranges"],
            )
        else:
            predicted_resource = {
                name: float(raw_resource[name]) * float(scalar[name])
                for name in RESOURCES
            }
        utilization = {
            name: predicted_resource[name] / common.CAPABILITY[name]
            for name in RESOURCES
        }
        if any(
            utilization[name] > SAFE_FRONTIER_MAX_UTILIZATION[name]
            for name in RESOURCES
        ):
            continue
        local_ranking_trusted = bool(
            knn_audit is not None
            and float(knn_audit["nearest_normalized_distance"])
            <= MAX_TRUSTED_KNN_DISTANCE
            and max(
                float(value)
                for value in knn_audit["relative_resource_span"].values()
            )
            <= MAX_RANKING_RELATIVE_SPAN
            and (
                knn_audit.get("exact_observation_match") is not True
                or knn_audit.get("exact_repeat_stable") is True
            )
        )
        trusted = bool(regime_trusted and local_ranking_trusted)
        fp = common.fingerprint(plan, feature_ratio, predicted_resource)
        if fp["id"] in seen_ids and not allow_seen:
            continue
        seen_ids.add(fp["id"])
        relative_span = (
            knn_audit["relative_resource_span"]
            if knn_audit is not None
            else (
                regime["relative_resource_span"]
                if regime is not None
                else {name: 1.0 for name in RESOURCES}
            )
        )
        interval = {
            name: {
                "lower": max(
                    0.0,
                    predicted_resource[name] * (1.0 - relative_span[name]),
                ),
                "median": predicted_resource[name],
                "upper": predicted_resource[name] * (1.0 + relative_span[name]),
                "relative_span": relative_span[name],
            }
            for name in RESOURCES
        }
        predicted_score = common.score(predicted_resource)
        predicted_score_interval = conservative_score_interval(interval)
        empirical_score = (
            float(regime["actual_score_median"])
            if regime is not None
            else predicted_score
        )
        uncertainty_penalty = (
            predicted_score - float(predicted_score_interval["lower"])
        )
        # Formal acquisition is ordered by the conservative score lower bound,
        # not by an optimistic point estimate.  The interval comes from the
        # exact plan regime's observed resource spread.
        ranking_score = float(predicted_score_interval["lower"])
        predicted_improvement = (
            predicted_score - formal_incumbent_score
            if formal_incumbent_score is not None
            else None
        )
        predicted_improvement_lower_bound = (
            ranking_score - formal_incumbent_score
            if formal_incumbent_score is not None
            else None
        )
        candidate = copy.deepcopy(source)
        inside_formal_range = (
            all(
                float(bounds["min_value"])
                < float(params[name])
                < float(bounds["max_value"])
                for name, bounds in formal_ranges_at_generation.items()
            )
            if formal_ranges_at_generation is not None
            else None
        )
        candidate.update(
            {
                "source_provenance": provenance,
                # The candidate is a fresh EXPLAIN result for ``params``.
                # Keeping candidate_origin from the copied source row can
                # falsely label a strict-interior Halton/staircase point as a
                # historical boundary corner.  Make provenance authoritative
                # and let the independently computed boundary_corner flag
                # describe the current parameter vector.
                "candidate_origin": provenance,
                "selection_role": "trusted" if trusted else "uncertain_probe",
                "trusted": trusted,
                "uncertain_probe": not trusted,
                "exact_plan_regime_validated": regime is not None,
                "optimized_params": params,
                "params": params,
                "feature_ratio": feature_ratio,
                "uncalibrated_predicted_resource": {
                    name: float(raw_resource[name]) for name in RESOURCES
                },
                "predicted_resource": predicted_resource,
                "predicted_resource_interval": interval,
                "final_resource": predicted_resource,
                "utilization": utilization,
                "candidate_score": predicted_score,
                "predicted_score": predicted_score,
                "predicted_score_interval": predicted_score_interval,
                "formal_lcb_score": ranking_score,
                "final_score": predicted_score,
                "robust_regime_ranking_score": ranking_score,
                "plan_regime_empirical_score": empirical_score,
                "predicted_improvement_over_incumbent": (
                    predicted_improvement
                ),
                "predicted_improvement_lower_bound": (
                    predicted_improvement_lower_bound
                ),
                "formal_improvement_eligible": bool(
                    trusted
                    and predicted_improvement_lower_bound is not None
                    and predicted_improvement_lower_bound
                    > MIN_PREDICTED_IMPROVEMENT
                ),
                "calibration_uncertainty_penalty": uncertainty_penalty,
                "staircase_search": (
                    {
                        **copy.deepcopy(staircase),
                        "observed_plan": plan,
                        "first_legal_plan_change": first_plan_change,
                    }
                    if staircase is not None
                    else None
                ),
                "delta_param": common.delta_param(baseline_params, params),
                "delta_param_from_formal_incumbent": (
                    copy.deepcopy(formal_relative_step)
                    if formal_relative_step is not None
                    else None
                ),
                "maximum_relative_step_from_formal_incumbent": (
                    max(abs(value) for value in formal_relative_step.values())
                    if formal_relative_step is not None
                    else None
                ),
                "safe_frontier": {
                    "passed": True,
                    "maximum_relative_step": (
                        MAX_RELATIVE_STEP_FROM_FORMAL_INCUMBENT
                    ),
                    "maximum_utilization": copy.deepcopy(
                        SAFE_FRONTIER_MAX_UTILIZATION
                    ),
                },
                "workload_plan_fingerprint": plan,
                "three_level_fingerprint": fp,
                "boundary_plateau": False,
                "boundary_corner": not common.is_non_corner(
                    params, config["param_ranges"]
                ),
                "probe_kind": (
                    "robust_exact_regime_candidate"
                    if trusted
                    else "unstable_or_unknown_regime_probe"
                ),
                "incumbent_update_eligible": False,
                "diagnostic_only": True,
                "qps_used_for_inner_scoring": False,
                "inside_formal_range_at_generation": inside_formal_range,
                "plan_regime_calibration": {
                    "mode": (
                        "validated_exact_candidate_observation"
                        if validated_replay_evidence is not None
                        else (
                            "exact_plan_parameter_knn_with_interval"
                            if knn_audit is not None
                            else (
                                "exact_regime_componentwise_median_with_interval"
                                if regime is not None
                                else "unknown_plan_regime_probe_only"
                            )
                        )
                    ),
                    "trusted_eligible": trusted,
                    "regime": copy.deepcopy(regime),
                    "candidate_level_knn": copy.deepcopy(knn_audit),
                    "validated_replay_evidence": copy.deepcopy(
                        validated_replay_evidence
                    ),
                },
                "prediction_source": (
                    "validated_exact_candidate_actual_resource"
                    if validated_replay_evidence is not None
                    else (
                        "exact_plan_parameter_knn"
                        if knn_audit is not None
                        else (
                            "exact_regime_componentwise_median"
                            if regime is not None
                            else "unknown_plan_regime_probe_only"
                        )
                    )
                ),
                "adaptive_gradient_expansion": copy.deepcopy(
                    source.get("adaptive_gradient_expansion")
                ),
            }
        )
        if candidate["boundary_corner"]:
            continue
        for required in (
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
            "three_level_fingerprint",
        ):
            if candidate.get(required) is None:
                raise RuntimeError(f"candidate missing required field: {required}")
        if (
            candidate["three_level_fingerprint"]["id"]
            in EXTRA_EXCLUDED_FINGERPRINTS
        ):
            continue
        candidates.append(candidate)

    trusted_candidates = [item for item in candidates if item["trusted"]]
    credible_frontier_score = (
        max(float(item["predicted_score"]) for item in trusted_candidates)
        if trusted_candidates
        else formal_incumbent_score
    )
    score_ladder_target = None
    score_ladder_target_gain = None
    score_ladder_frontier_gain = None
    score_ladder_remaining_accepts = None
    if (
        formal_incumbent_score is not None
        and formal_accepted_count is not None
        and credible_frontier_score is not None
    ):
        ladder = compute_score_ladder(
            formal_incumbent_score,
            formal_accepted_count,
            credible_frontier_score,
            observed_replay_noise_floor,
        )
        score_ladder_remaining_accepts = int(ladder["remaining_accepts"])
        score_ladder_frontier_gain = float(ladder["frontier_gain"])
        score_ladder_target_gain = float(ladder["target_gain"])
        score_ladder_target = float(ladder["target_score"])
        for item in candidates:
            predicted = float(item["predicted_score"])
            lower = float(item["robust_regime_ranking_score"])
            item["score_ladder"] = {
                "contract": "remaining_accepts_credible_frontier_ladder_v1",
                "accepted_count": formal_accepted_count,
                "accepted_target": ACCEPTED_TARGET,
                "remaining_accepts": score_ladder_remaining_accepts,
                "safety_margin": SCORE_LADDER_SAFETY_MARGIN,
                "incumbent_score": formal_incumbent_score,
                "credible_frontier_score": credible_frontier_score,
                "frontier_gain": score_ladder_frontier_gain,
                "target_gain": score_ladder_target_gain,
                "target_score": score_ladder_target,
                "predicted_score": predicted,
                "conservative_lower_score": lower,
                "distance_to_target": abs(predicted - score_ladder_target),
                "predicted_above_incumbent": (
                    predicted > formal_incumbent_score
                ),
                "conservative_lower_above_incumbent": (
                    lower > formal_incumbent_score
                ),
            }

    trusted_pool = sorted(
        (item for item in candidates if item["trusted"]),
        key=lambda item: (
            -int(
                FORMAL_EXPECTED_IMPROVEMENT_REQUIRED
                and item["formal_improvement_eligible"]
            ),
            (
                0
                if formal_incumbent_score is None
                or float(item["robust_regime_ranking_score"])
                > formal_incumbent_score
                else (
                    1
                    if float(item["predicted_score"])
                    > formal_incumbent_score
                    else 2
                )
            ),
            (
                abs(
                    float(item["predicted_score"])
                    - float(score_ladder_target)
                )
                if score_ladder_target is not None
                else 0.0
            ),
            -float(item["robust_regime_ranking_score"]),
            -float(item["candidate_score"]),
            item["source_provenance"],
        ),
    )
    trusted: List[Dict[str, Any]] = []
    if not DIAGNOSTIC_PROBE_ONLY:
        trusted_plans = set()
        for candidate in trusted_pool:
            plan = candidate["workload_plan_fingerprint"]
            if TRUSTED_PLAN_DIVERSITY_REQUIRED and plan in trusted_plans:
                continue
            trusted.append(candidate)
            trusted_plans.add(plan)
            if len(trusted) == 3:
                break
        # Plan diversity is a preference, not an entry-contract requirement.
        # A narrow, correctly recentered formal range can contain only one
        # validated plan while still exposing several novel feature/resource
        # fingerprints.  In that case fill the remaining trusted slots from
        # the same exact validated regime using maximin parameter separation.
        # These rows are already trusted by the exact-regime/KNN checks above;
        # this fallback never promotes an unknown or unstable regime.
        if len(trusted) < 3:
            selected_ids = {
                item["three_level_fingerprint"]["id"] for item in trusted
            }
            remaining = [
                item
                for item in trusted_pool
                if item["three_level_fingerprint"]["id"] not in selected_ids
            ]
            while remaining and len(trusted) < 3:
                source = max(
                    remaining,
                    key=lambda item: (
                        min(
                            normalized_parameter_distance(
                                item["optimized_params"],
                                selected["optimized_params"],
                                config["param_ranges"],
                            )
                            for selected in trusted
                        )
                        if trusted
                        else math.inf,
                        float(item["robust_regime_ranking_score"]),
                        float(item["candidate_score"]),
                    ),
                )
                remaining.remove(source)
                selected = copy.deepcopy(source)
                selected["trusted_plan_diversity_fallback"] = {
                    "applied": True,
                    "reason": (
                        "fewer_than_three_validated_plans_inside_"
                        "authoritative_formal_range"
                    ),
                    "plan_remains_exactly_validated": True,
                    "unknown_regime_promoted": False,
                }
                trusted.append(selected)
                selected_ids.add(selected["three_level_fingerprint"]["id"])
        trusted.sort(
            key=lambda item: (
                -int(item["formal_improvement_eligible"]),
                -float(item["robust_regime_ranking_score"]),
                -float(item["predicted_score"]),
                item["source_provenance"],
            )
        )
        if len(trusted) == 3:
            trusted_score_span = max(
                float(item["predicted_score"]) for item in trusted
            ) - min(float(item["predicted_score"]) for item in trusted)
            if trusted_score_span <= effective_min_trusted_score_span:
                raise RuntimeError(
                    "trusted ranking signal is not distinguishable from "
                    "replay noise: "
                    f"span={trusted_score_span:.12g} "
                    "noise_bound="
                    f"{effective_min_trusted_score_span:.12g}"
                )
    unstable_pool = sorted(
        (
            item
            for item in candidates
            if not item["trusted"]
            and item["workload_plan_fingerprint"] in regimes
        ),
        key=lambda item: (
            -max(
                item["plan_regime_calibration"]["regime"][
                    "relative_resource_span"
                ].values()
            ),
            item["source_provenance"],
        ),
    )
    unknown_pool = sorted(
        (
            item
            for item in candidates
            if item["workload_plan_fingerprint"] not in regimes
        ),
        key=lambda item: (-float(item["candidate_score"]), item["source_provenance"]),
    )
    probes = [] if PROBES_MUST_BE_UNKNOWN else unstable_pool[:1]
    if ALLOW_SPARSE_EXACT_REGIME_PROBES and not PROBES_MUST_BE_UNKNOWN:
        trusted_ids = {
            item["three_level_fingerprint"]["id"] for item in trusted
        }
        sparse_exact_pool = sorted(
            (
                item
                for item in trusted_pool
                if item["three_level_fingerprint"]["id"] not in trusted_ids
                and int(
                    item["plan_regime_calibration"]["regime"][
                        "observation_count"
                    ]
                )
                <= SPARSE_EXACT_REGIME_MAX_OBSERVATIONS
            ),
            key=lambda item: (
                int(
                    item["plan_regime_calibration"]["regime"][
                        "observation_count"
                    ]
                ),
                -float(item["candidate_score"]),
                item["source_provenance"],
            ),
        )
        for source in sparse_exact_pool:
            if len(probes) == 2:
                break
            probe = copy.deepcopy(source)
            probe.update(
                {
                    "selection_role": "uncertain_probe",
                    "trusted": False,
                    "uncertain_probe": True,
                    "probe_kind": "sparse_exact_plan_regime_probe",
                    "probe_reason": (
                        "exact_plan_regime_has_only_"
                        f"{probe['plan_regime_calibration']['regime']['observation_count']}"
                        "_clean_observation"
                    ),
                    "incumbent_update_eligible": False,
                    "diagnostic_only": True,
                }
            )
            probes.append(probe)
    if DIAGNOSTIC_PROBE_ONLY:
        # A failed stability gate needs acquisition data, not synthetic trust.
        # Select five novel probes with maximin parameter diversity from every
        # available regime.  Their replay can validate new exact regimes for a
        # later 3-trusted + 2-probe entry batch, but can never mutate formal
        # state itself.
        probe_pool = sorted(
            candidates,
            key=lambda item: (
                item["exact_plan_regime_validated"] is True,
                -max(
                    float(value)
                    for value in (
                        item["plan_regime_calibration"].get("regime") or {}
                    ).get(
                        "relative_resource_span",
                        {name: 1.0 for name in RESOURCES},
                    ).values()
                ),
                -float(item["candidate_score"]),
                item["source_provenance"],
            ),
        )

        def normalized_distance(
            left: Mapping[str, Any],
            right: Mapping[str, Any],
        ) -> float:
            squared = 0.0
            for name, bounds in config["param_ranges"].items():
                span = float(bounds["max_value"]) - float(bounds["min_value"])
                if span <= 0.0:
                    continue
                delta = (
                    float(left["optimized_params"][name])
                    - float(right["optimized_params"][name])
                ) / span
                squared += delta * delta
            return math.sqrt(squared)

        selected_probes: List[Dict[str, Any]] = []
        ranked_by_score = sorted(
            probe_pool,
            key=lambda item: (
                -float(item["candidate_score"]),
                item["three_level_fingerprint"]["id"],
            ),
        )
        if not ranked_by_score:
            raise RuntimeError("empty diagnostic probe pool")
        pool_score_span = (
            float(ranked_by_score[0]["candidate_score"])
            - float(ranked_by_score[-1]["candidate_score"])
        )
        if pool_score_span <= MINIMUM_DIAGNOSTIC_SCORE_SPAN:
            raise RuntimeError(
                "diagnostic candidate pool score span is not distinguishable "
                "from replay noise: "
                f"span={pool_score_span:.12g} "
                f"noise_bound={MINIMUM_DIAGNOSTIC_SCORE_SPAN:.12g}"
            )

        # Parameter-only maximin can select five points whose scores all sit
        # inside one replay-noise band.  Seed deterministic score anchors over
        # the full pool span (best, worst, median), then fill the two remaining
        # slots by parameter-space maximin.
        anchor_indices = (0, len(ranked_by_score) - 1, len(ranked_by_score) // 2)
        anchor_ids: set[str] = set()
        for index in anchor_indices:
            source = ranked_by_score[index]
            identifier = source["three_level_fingerprint"]["id"]
            if identifier in anchor_ids:
                continue
            anchor_ids.add(identifier)
            selected_probes.append(copy.deepcopy(source))
        remaining = [
            item
            for item in probe_pool
            if item["three_level_fingerprint"]["id"] not in anchor_ids
        ]
        while remaining and len(selected_probes) < 5:
            source = max(
                remaining,
                key=lambda item: (
                    min(
                        normalized_distance(item, selected)
                        for selected in selected_probes
                    ),
                    item["exact_plan_regime_validated"] is False,
                    float(item["candidate_score"]),
                    item["three_level_fingerprint"]["id"],
                ),
            )
            remaining.remove(source)
            probe = copy.deepcopy(source)
            probe.update(
                {
                    "selection_role": "uncertain_probe",
                    "trusted": False,
                    "uncertain_probe": True,
                    "probe_kind": "autonomous_gate_recovery_probe",
                    "probe_reason": "stability_gate_failed_requires_new_evidence",
                    "incumbent_update_eligible": False,
                    "diagnostic_only": True,
                }
            )
            selected_probes.append(probe)
        for probe in selected_probes:
            probe.update(
                {
                    "selection_role": "uncertain_probe",
                    "trusted": False,
                    "uncertain_probe": True,
                    "probe_kind": "autonomous_gate_recovery_probe",
                    "probe_reason": "stability_gate_failed_requires_new_evidence",
                    "incumbent_update_eligible": False,
                    "diagnostic_only": True,
                }
            )
        probes = selected_probes
        require_five_diagnostic_probes(probes)
    else:
        for candidate in unknown_pool:
            if len(probes) == 2:
                break
            if candidate["workload_plan_fingerprint"] not in {
                item["workload_plan_fingerprint"] for item in probes
            }:
                probes.append(candidate)
        # If no unknown regime remains, use a second distinct unstable exact
        # regime as the uncertainty probe.  It remains probe-only and cannot
        # mutate gradient/incumbent/accepted state.
        for candidate in unstable_pool:
            if len(probes) == 2:
                break
            if (
                candidate["three_level_fingerprint"]["id"]
                not in {
                    item["three_level_fingerprint"]["id"]
                    for item in probes
                }
                and candidate["workload_plan_fingerprint"]
                not in {
                    item["workload_plan_fingerprint"] for item in probes
                }
            ):
                probes.append(candidate)
        # A formal batch still reserves two non-mutating uncertainty slots.
        # Once discovery has validated nearly every plan regime, an
        # "unknown-plan only" rule can make the acquisition pool impossible
        # even though there are novel candidate-level extrapolations in known
        # regimes.  Use the least-observed, maximin-separated calibrated
        # candidates as probe-only points.  They never become trusted merely
        # because their plan is known and cannot update formal state.
        if ALLOW_SPARSE_EXACT_REGIME_PROBES and len(probes) < 2:
            selected_ids = {
                item["three_level_fingerprint"]["id"]
                for item in [*trusted, *probes]
            }
            selected_params = [*trusted, *probes]

            def probe_parameter_distance(
                left: Mapping[str, Any],
                right: Mapping[str, Any],
            ) -> float:
                squared = 0.0
                for name, bounds in config["param_ranges"].items():
                    span = (
                        float(bounds["max_value"])
                        - float(bounds["min_value"])
                    )
                    if span <= 0.0:
                        continue
                    delta = (
                        float(left["optimized_params"][name])
                        - float(right["optimized_params"][name])
                    ) / span
                    squared += delta * delta
                return math.sqrt(squared)

            remaining = [
                item
                for item in trusted_pool
                if item["three_level_fingerprint"]["id"] not in selected_ids
            ]
            while remaining and len(probes) < 2:
                source = max(
                    remaining,
                    key=lambda item: (
                        -int(
                            item["plan_regime_calibration"]["regime"][
                                "observation_count"
                            ]
                        ),
                        min(
                            probe_parameter_distance(item, selected)
                            for selected in selected_params
                        ),
                        max(
                            float(value)
                            for value in item["plan_regime_calibration"][
                                "regime"
                            ]["relative_resource_span"].values()
                        ),
                        float(item["candidate_score"]),
                    ),
                )
                remaining.remove(source)
                probe = copy.deepcopy(source)
                observation_count = int(
                    probe["plan_regime_calibration"]["regime"][
                        "observation_count"
                    ]
                )
                probe.update(
                    {
                        "selection_role": "uncertain_probe",
                        "trusted": False,
                        "uncertain_probe": True,
                        "probe_kind": (
                            "known_plan_candidate_level_extrapolation_probe"
                        ),
                        "probe_reason": (
                            "known_plan_but_candidate_level_prediction_unseen"
                        ),
                        "candidate_trust_confidence": min(
                            0.49, 1.0 / (observation_count + 1.0)
                        ),
                        "incumbent_update_eligible": False,
                        "diagnostic_only": True,
                        "formal_improvement_eligible": False,
                    }
                )
                probes.append(probe)
                selected_params.append(probe)
                selected_ids.add(probe["three_level_fingerprint"]["id"])
        if len(trusted) != 3 or len(probes) != 2:
            candidate_plan_counts: Dict[str, int] = {}
            for item in candidates:
                plan = item["workload_plan_fingerprint"]
                candidate_plan_counts[plan] = (
                    candidate_plan_counts.get(plan, 0) + 1
                )
            raise RuntimeError(
                "insufficient robust pool: "
                f"trusted={len(trusted)}, probes={len(probes)}, "
                f"candidate_count={len(candidates)}, "
                f"trusted_pool={len(trusted_pool)}, "
                f"candidate_plan_counts={candidate_plan_counts}"
            )
        if (
            FORMAL_EXPECTED_IMPROVEMENT_REQUIRED
            and not any(
                item["formal_improvement_eligible"] for item in trusted
            )
        ):
            raise RuntimeError(
                "formal acquisition exhausted: no trusted candidate has "
                "positive predicted improvement over incumbent"
            )
    top5 = trusted + probes
    for rank, candidate in enumerate(top5, 1):
        candidate["rank"] = rank
    roles = [item["selection_role"] for item in top5]
    ids = [item["three_level_fingerprint"]["id"] for item in top5]
    expected_roles = (
        ["uncertain_probe"] * 5
        if DIAGNOSTIC_PROBE_ONLY
        else ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
    )
    if roles != expected_roles:
        raise RuntimeError("Top-5 role contract failure")
    if len(set(ids)) != 5 or (
        not ALLOW_VALIDATED_ANCHOR_REPLAY
        and any(item in historical_ids for item in ids)
    ):
        raise RuntimeError("Top-5 fingerprint novelty failure")
    if (
        not ALLOW_VALIDATED_ANCHOR_REPLAY
        and any(
            parameter_key(item["optimized_params"]) in historical_params
            for item in top5
        )
    ):
        raise RuntimeError("Top-5 parameter novelty failure")

    output = {
        "schema_version": 3,
        "selection_contract": "tpcc_robust_exact_plan_regime_trusted3_probe2_v1",
        "diagnostic_only": True,
        "state_mutation_contract": {
            "accepted_update": False,
            "incumbent_update": False,
            "range_update": False,
            "gradient_update": False,
        },
        "qps_role": "outer_audit_only_not_inner_score",
        "maximum_real_workload_invocations": 5,
        "source_history": str(common.HISTORY),
        "source_history_sha256": common.sha256(common.HISTORY),
        "source_ranking_line_number": ranking["_source_line_number"],
        "source_config": str(common.CONFIG),
        "source_config_sha256": common.sha256(common.CONFIG),
        "formal_range_state_source": (
            str(PARAM_RANGES_OVERRIDE.resolve())
            if PARAM_RANGES_OVERRIDE is not None
            else None
        ),
        "formal_range_state_source_sha256": range_override_sha256,
        "formal_state_audit_source": (
            str(FORMAL_STATE_AUDIT_SOURCE.resolve())
            if FORMAL_STATE_AUDIT_SOURCE is not None
            else None
        ),
        "formal_state_audit_source_sha256": (
            common.sha256(FORMAL_STATE_AUDIT_SOURCE.resolve())
            if FORMAL_STATE_AUDIT_SOURCE is not None
            else None
        ),
        "formal_range_state_at_generation": (
            copy.deepcopy(formal_ranges_at_generation)
            if FORMAL_STATE_AUDIT_SOURCE is not None
            else None
        ),
        "formal_incumbent_score_at_generation": formal_incumbent_score,
        "formal_incumbent_params_at_generation": copy.deepcopy(
            formal_incumbent_params
        ),
        "formal_accepted_count_at_generation": formal_accepted_count,
        "score_ladder": {
            "contract": "remaining_accepts_credible_frontier_ladder_v1",
            "accepted_target": ACCEPTED_TARGET,
            "accepted_count": formal_accepted_count,
            "remaining_accepts": score_ladder_remaining_accepts,
            "safety_margin": SCORE_LADDER_SAFETY_MARGIN,
            "credible_frontier_score": credible_frontier_score,
            "frontier_gain": score_ladder_frontier_gain,
            "target_gain": score_ladder_target_gain,
            "target_score": score_ladder_target,
            "observed_replay_noise_floor": observed_replay_noise_floor,
            "manual_candidate_injection": False,
        },
        "diagnostic_joint_near_vertex_enumeration": {
            "contract": "complete_2pow_parameter_near_vertex_enumeration_v1",
            "parameter_count": len(config["param_ranges"]),
            "combination_count": 1 << len(config["param_ranges"]),
            "strict_boundary_margin": 0.01,
        },
        "diagnostic_adaptive_gradient_expansion": (
            adaptive_expansion_audit
        ),
        "formal_expected_improvement_required": (
            FORMAL_EXPECTED_IMPROVEMENT_REQUIRED
        ),
        "minimum_predicted_improvement": MIN_PREDICTED_IMPROVEMENT,
        "formal_admission_score": "predicted_score_interval.lower",
        "staircase_search_contract": (
            "within_exact_plan_geometric_staircase_first_change_v1"
        ),
        "candidate_range_policy": CANDIDATE_RANGE_POLICY,
        "candidate_param_ranges": copy.deepcopy(config["param_ranges"]),
        "source_discovery": str(SOURCES[0][0]),
        "source_discovery_sha256": common.sha256(SOURCES[0][0]),
        "source_discovery_ledger": str(SOURCES[0][1]),
        "source_discovery_ledger_sha256": common.sha256(SOURCES[0][1]),
        "source_batches": source_manifest,
        "robust_regimes": regimes,
        "trusted_relative_span_limit": MAX_TRUSTED_RELATIVE_SPAN,
        "ranking_relative_span_limit": MAX_RANKING_RELATIVE_SPAN,
        "trusted_knn_distance_limit": MAX_TRUSTED_KNN_DISTANCE,
        "minimum_exact_point_observations": MIN_EXACT_POINT_OBSERVATIONS,
        "minimum_trusted_predicted_score_span": (
            MIN_TRUSTED_PREDICTED_SCORE_SPAN
        ),
        "observed_exact_repeat_score_noise_floor": (
            observed_replay_noise_floor
        ),
        "effective_minimum_trusted_predicted_score_span": (
            effective_min_trusted_score_span
        ),
        "maximum_relative_step_from_formal_incumbent": (
            MAX_RELATIVE_STEP_FROM_FORMAL_INCUMBENT
        ),
        "safe_frontier_max_utilization": copy.deepcopy(
            SAFE_FRONTIER_MAX_UTILIZATION
        ),
        "single_observation_relative_half_width": (
            SINGLE_OBSERVATION_RELATIVE_HALF_WIDTH
        ),
        "extra_excluded_fingerprints": sorted(
            EXTRA_EXCLUDED_FINGERPRINTS
        ),
        "candidate_pool_size": len(candidates),
        "top5": top5,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "sha256": common.sha256(OUTPUT),
                "candidate_pool_size": len(candidates),
                "roles": roles,
                "plans": [item["workload_plan_fingerprint"] for item in top5],
                "fingerprints": ids,
                "ranking_scores": [
                    item["robust_regime_ranking_score"] for item in top5
                ],
                "predicted_scores": [item["predicted_score"] for item in top5],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
