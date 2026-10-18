#!/usr/bin/env python3
"""Build a non-mutating EXPLAIN-only TPC-C range-expansion discovery.

Each point moves exactly one parameter outside the current formal range by
1, 2, or 4 transformed source-grid steps while remaining a non-corner point
inside the immutable source configuration bounds.  Formal state and formal
range files are read-only.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_batch06_prep_rangebound_discovery as discovery


robust = discovery.robust
common = discovery.common
ATTEMPT = discovery.ATTEMPT
FORMAL_STATE = discovery.FORMAL_STATE
EXPECTED_FORMAL_STATE_SHA256 = discovery.EXPECTED_FORMAL_STATE_SHA256
OUTPUT = Path(
    "/tmp/tpcc_batch06_prep_range_expansion_probe_top5_v1.json"
)
FAIL_EVIDENCE = Path(
    "/tmp/tpcc_batch06_prep_range_expansion_probe_failure_evidence_v1.json"
)
STEP_MULTIPLIERS = (1, 2, 4)
LEVEL_QUANTA = discovery.LEVEL_QUANTA
RESOURCES = common.RESOURCES


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def outside_formal_details(
    params: Mapping[str, Any],
    formal_ranges: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    details = {}
    for name, bounds in formal_ranges.items():
        value = float(params[name])
        lower = float(bounds["min_value"])
        upper = float(bounds["max_value"])
        if value < lower:
            details[name] = {
                "direction": "lower",
                "value": value,
                "formal_bound": lower,
            }
        elif value > upper:
            details[name] = {
                "direction": "upper",
                "value": value,
                "formal_bound": upper,
            }
    return details


def expansion_samples(
    source_ranges: Mapping[str, Any],
    formal_ranges: Mapping[str, Any],
    incumbent: Mapping[str, Any],
) -> List[Tuple[str, Dict[str, float], Dict[str, Any]]]:
    center = common.clipped_non_corner(incumbent, source_ranges)
    margin = float(common.NON_CORNER_MARGIN) + 1e-6
    samples = []
    for name in sorted(source_ranges):
        source_bounds = source_ranges[name]
        discrete_count = max(
            1,
            int(source_bounds.get("discrete_count") or 20),
        )
        grid_step = 1.0 / discrete_count
        formal_lower_fraction = common.transformed_fraction(
            name,
            float(formal_ranges[name]["min_value"]),
            source_bounds,
        )
        formal_upper_fraction = common.transformed_fraction(
            name,
            float(formal_ranges[name]["max_value"]),
            source_bounds,
        )
        for direction in ("lower", "upper"):
            boundary_fraction = (
                formal_lower_fraction
                if direction == "lower"
                else formal_upper_fraction
            )
            for step_multiplier in STEP_MULTIPLIERS:
                signed = (
                    -step_multiplier * grid_step
                    if direction == "lower"
                    else step_multiplier * grid_step
                )
                target_fraction = min(
                    1.0 - margin,
                    max(margin, boundary_fraction + signed),
                )
                if (
                    direction == "lower"
                    and target_fraction >= formal_lower_fraction
                ) or (
                    direction == "upper"
                    and target_fraction <= formal_upper_fraction
                ):
                    continue
                params = dict(center)
                params[name] = discovery.value_at_fraction(
                    name,
                    target_fraction,
                    source_bounds,
                )
                details = outside_formal_details(params, formal_ranges)
                if set(details) != {name}:
                    raise RuntimeError(
                        f"range-expansion point is not one-directional: {details}"
                    )
                if not common.is_non_corner(params, source_ranges):
                    raise RuntimeError("range-expansion point is a source corner")
                metadata = {
                    "expanded_parameter": name,
                    "direction": direction,
                    "step_multiplier": step_multiplier,
                    "source_grid_step": grid_step,
                    "source_transformed_fraction": target_fraction,
                    "formal_boundary_transformed_fraction": boundary_fraction,
                    "outside_formal_range": True,
                    "outside_formal_range_details": details,
                }
                provenance = (
                    f"expand_{name}_{direction}_step{step_multiplier}"
                )
                samples.append((provenance, params, metadata))
    return samples


def exact_signature(candidate: Mapping[str, Any]) -> Tuple[Any, ...]:
    return (
        candidate["workload_plan_fingerprint"],
        tuple(
            float(candidate["feature_ratio"][name]).hex()
            for name in RESOURCES
        ),
        tuple(
            (
                float(candidate["predicted_resource"][name])
                / common.CAPABILITY[name]
            ).hex()
            for name in RESOURCES
        ),
    )


def euclidean(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(
        sum((float(a) - float(b)) ** 2 for a, b in zip(left, right))
    )


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    if common.sha256(FORMAL_STATE) != EXPECTED_FORMAL_STATE_SHA256:
        raise RuntimeError("formal state SHA changed before expansion probe")
    state = load_json(FORMAL_STATE)
    if (
        state.get("status") != "ready"
        or state.get("accepted_count") != 1
        or state.get("formal_physical_call_count") != 25
        or state.get("formal_valid_replay_count") != 25
        or state.get("formal_technical_failure_count") != 0
        or state.get("qps_used_for_inner_scoring") is not False
    ):
        raise RuntimeError("formal state is not clean post-batch05 state")
    formal_ranges = copy.deepcopy(state["range_state"])
    config = load_json(common.CONFIG)
    source_ranges = copy.deepcopy(config["param_ranges"])
    if set(source_ranges) != set(formal_ranges):
        raise RuntimeError("source/formal parameter sets differ")

    regimes, historical_ids, historical_params, source_manifest = (
        discovery.historical_context()
    )
    ranking = common.load_ranking()
    source_anchor = ranking["candidate_rankings"][0]
    baseline_params = {
        name: float(value)
        for name, value in source_anchor["initial_params"].items()
    }
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
    estimator.calibrate(
        queries,
        baseline_params,
        source_anchor["initial_resource"],
    )

    names = tuple(sorted(source_ranges))
    candidates: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    seen_params = set(historical_params)
    samples = expansion_samples(
        source_ranges,
        formal_ranges,
        state["incumbent_params"],
    )
    for provenance, params, expansion in samples:
        params_id = robust.parameter_key(params)
        if params_id in seen_params:
            continue
        seen_params.add(params_id)
        try:
            raw_resource, _stats, estimator_metadata = (
                estimator.estimate_with_metadata(queries, params)
            )
        except Exception as exc:
            errors.append(
                {
                    "source_provenance": provenance,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        plan = str(
            estimator_metadata.get("workload_plan_fingerprint") or ""
        )
        if not plan.startswith("ratio-plan-sha256:"):
            errors.append(
                {
                    "source_provenance": provenance,
                    "error": "missing workload plan fingerprint",
                }
            )
            continue
        if plan in regimes:
            continue
        feature_ratio = {
            name: float(estimator_metadata["feature_ratios"][name])
            for name in RESOURCES
        }
        predicted_resource = {
            name: float(raw_resource[name]) for name in RESOURCES
        }
        score = common.score(predicted_resource)
        candidate = copy.deepcopy(source_anchor)
        candidate.update(
            {
                "source_provenance": provenance,
                "selection_role": "uncertain_probe",
                "trusted": False,
                "uncertain_probe": True,
                "exact_plan_regime_validated": False,
                "optimized_params": params,
                "params": params,
                "feature_ratio": feature_ratio,
                "uncalibrated_predicted_resource": predicted_resource,
                "predicted_resource": predicted_resource,
                "predicted_resource_interval": {
                    name: {
                        "lower": 0.0,
                        "median": predicted_resource[name],
                        "upper": predicted_resource[name] * 2.0,
                        "relative_span": 1.0,
                    }
                    for name in RESOURCES
                },
                "final_resource": predicted_resource,
                "utilization": {
                    name: predicted_resource[name] / common.CAPABILITY[name]
                    for name in RESOURCES
                },
                "candidate_score": score,
                "predicted_score": score,
                "final_score": score,
                "delta_param": common.delta_param(
                    state["incumbent_params"],
                    params,
                ),
                "eta": 0.0,
                "workload_plan_fingerprint": plan,
                "boundary_plateau": False,
                "boundary_corner": not common.is_non_corner(
                    params,
                    source_ranges,
                ),
                "outside_formal_range": True,
                "outside_formal_range_details": expansion[
                    "outside_formal_range_details"
                ],
                "range_expansion_probe": expansion,
                "probe_kind": (
                    "nonmutating_one_parameter_range_expansion_unknown_plan"
                ),
                "incumbent_update_eligible": False,
                "diagnostic_only": True,
                "qps_used_for_inner_scoring": False,
                "plan_regime_calibration": {
                    "mode": "unknown_plan_regime_probe_only",
                    "trusted_eligible": False,
                    "regime": None,
                },
                "_source_coordinates": discovery.normalized_coordinates(
                    params,
                    source_ranges,
                    names,
                ),
            }
        )
        if candidate["boundary_corner"]:
            raise RuntimeError("expansion candidate is a source corner")
        candidates.append(candidate)

    exact_signatures = {exact_signature(item) for item in candidates}
    counts_by_quantum: Dict[str, int] = {}
    selected_quantum = None
    for quantum in LEVEL_QUANTA:
        ids = {
            discovery.adaptive_fingerprint(
                item["workload_plan_fingerprint"],
                item["feature_ratio"],
                item["predicted_resource"],
                quantum,
            )["id"]
            for item in candidates
        }
        counts_by_quantum[f"{quantum:.8f}"] = len(ids)
        if selected_quantum is None and len(ids) >= 5:
            selected_quantum = quantum

    response_ranges = {
        field: {
            name: {
                "min": min(float(item[field][name]) for item in candidates),
                "max": max(float(item[field][name]) for item in candidates),
            }
            for name in RESOURCES
        }
        for field in ("feature_ratio", "predicted_resource")
    } if candidates else {}
    evidence = {
        "schema_version": (
            "tpcc_batch06_prep_range_expansion_probe_evidence_v1"
        ),
        "formal_state_path": str(FORMAL_STATE),
        "formal_state_sha256": EXPECTED_FORMAL_STATE_SHA256,
        "formal_state_and_range_mutated": False,
        "immutable_source_config_path": str(common.CONFIG),
        "immutable_source_config_sha256": common.sha256(common.CONFIG),
        "step_multipliers": list(STEP_MULTIPLIERS),
        "attempted_point_count": len(samples),
        "raw_unknown_estimate_count": len(candidates),
        "unknown_plan_count": len({
            item["workload_plan_fingerprint"] for item in candidates
        }),
        "exact_continuous_signature_count": len(exact_signatures),
        "fingerprint_count_by_global_level_quantum": counts_by_quantum,
        "response_ranges": response_ranges,
        "explain_error_count": len(errors),
        "explain_errors": errors,
    }
    if selected_quantum is None or len(exact_signatures) < 5:
        FAIL_EVIDENCE.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(
            "range-expansion probe found fewer than five genuine responses; "
            f"evidence={FAIL_EVIDENCE}"
        )

    for candidate in candidates:
        candidate["three_level_fingerprint"] = (
            discovery.adaptive_fingerprint(
                candidate["workload_plan_fingerprint"],
                candidate["feature_ratio"],
                candidate["predicted_resource"],
                selected_quantum,
            )
        )
    representatives: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        fingerprint_id = candidate["three_level_fingerprint"]["id"]
        current = representatives.get(fingerprint_id)
        if current is None or (
            int(candidate["range_expansion_probe"]["step_multiplier"]),
            -float(candidate["candidate_score"]),
            candidate["source_provenance"],
        ) < (
            int(current["range_expansion_probe"]["step_multiplier"]),
            -float(current["candidate_score"]),
            current["source_provenance"],
        ):
            representatives[fingerprint_id] = candidate
    pool = list(representatives.values())
    minimal_step_cap = next(
        cap
        for cap in STEP_MULTIPLIERS
        if len({
            item["three_level_fingerprint"]["id"]
            for item in pool
            if int(item["range_expansion_probe"]["step_multiplier"]) <= cap
        }) >= 5
    )
    eligible = [
        item
        for item in pool
        if int(item["range_expansion_probe"]["step_multiplier"])
        <= minimal_step_cap
    ]
    selected = [
        max(
            eligible,
            key=lambda item: (
                float(item["candidate_score"]),
                item["source_provenance"],
            ),
        )
    ]
    while len(selected) < 5:
        remaining = [
            item
            for item in eligible
            if item["three_level_fingerprint"]["id"]
            not in {
                chosen["three_level_fingerprint"]["id"]
                for chosen in selected
            }
        ]
        selected.append(
            max(
                remaining,
                key=lambda item: (
                    min(
                        euclidean(
                            item["_source_coordinates"],
                            chosen["_source_coordinates"],
                        )
                        for chosen in selected
                    ),
                    float(item["candidate_score"]),
                    item["source_provenance"],
                ),
            )
        )
    for rank, candidate in enumerate(selected, 1):
        candidate["rank"] = rank
        candidate.pop("_source_coordinates", None)
    selected_ids = [
        item["three_level_fingerprint"]["id"] for item in selected
    ]
    if (
        len(set(selected_ids)) != 5
        or historical_ids.intersection(selected_ids)
        or any(
            item["workload_plan_fingerprint"] in regimes
            or item.get("outside_formal_range") is not True
            or len(item.get("outside_formal_range_details") or {}) != 1
            for item in selected
        )
    ):
        raise RuntimeError("range-expansion Top-5 contract failed")
    required_fields = (
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
    for candidate in selected:
        if any(candidate.get(name) is None for name in required_fields):
            raise RuntimeError(
                f"candidate audit field absent: rank={candidate['rank']}"
            )

    output = {
        "schema_version": 3,
        "selection_contract": "tpcc_round12_current_plan_discovery_probe5_v1",
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
        "source_generation_only_stop_line_number": 0,
        "source_config": str(common.CONFIG),
        "source_config_sha256": common.sha256(common.CONFIG),
        "formal_range_state_source": str(FORMAL_STATE),
        "formal_range_state_source_sha256": EXPECTED_FORMAL_STATE_SHA256,
        "formal_candidate_param_ranges": formal_ranges,
        "immutable_source_param_ranges": source_ranges,
        "source_batches": source_manifest,
        "known_exact_plan_regime_count": len(regimes),
        "range_expansion_contract": {
            "kind": (
                "nonmutating_one_parameter_transformed_source_grid_probe_v1"
            ),
            "formal_state_or_range_update": False,
            "step_multipliers": list(STEP_MULTIPLIERS),
            "minimal_step_cap_with_five_fingerprints": minimal_step_cap,
            "selected_global_level_quantum": selected_quantum,
            "all_points_outside_formal_range": True,
            "all_points_inside_immutable_source_noncorner": True,
        },
        "range_expansion_evidence": evidence,
        "top5": selected,
    }
    OUTPUT.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "sha256": common.sha256(OUTPUT),
                "minimal_step_cap": minimal_step_cap,
                "selected_quantum": selected_quantum,
                "fingerprints": selected_ids,
                "plans": [
                    item["workload_plan_fingerprint"] for item in selected
                ],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
