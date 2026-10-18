#!/usr/bin/env python3
"""Build TPC-C batch06-prep Top-5 using wide non-mutating EXPLAIN probes."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_batch06_prep_rangebound_discovery as discovery


robust = discovery.robust
common = discovery.common
ATTEMPT = discovery.ATTEMPT
FORMAL_STATE = discovery.FORMAL_STATE
FORMAL_STATE_SHA = discovery.EXPECTED_FORMAL_STATE_SHA256
OUTPUT = Path("/tmp/tpcc_batch06_prep_wide_expansion_top5_v1.json")
FAIL_EVIDENCE = Path(
    "/tmp/tpcc_batch06_prep_wide_expansion_failure_evidence_v1.json"
)
STEP_MULTIPLIERS = (8, 12, 16, 18)
RESOURCES = common.RESOURCES


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def transformed_value(
    name: str,
    direction: str,
    multiplier: int,
    source_bounds: Mapping[str, Any],
    formal_bounds: Mapping[str, Any],
) -> Optional[Tuple[float, Dict[str, Any]]]:
    discrete_count = max(1, int(source_bounds.get("discrete_count") or 20))
    grid_step = 1.0 / discrete_count
    formal_value = float(
        formal_bounds["min_value"]
        if direction == "lower"
        else formal_bounds["max_value"]
    )
    boundary_fraction = common.transformed_fraction(
        name,
        formal_value,
        source_bounds,
    )
    signed = -multiplier * grid_step if direction == "lower" else multiplier * grid_step
    margin = float(common.NON_CORNER_MARGIN) + 1e-6
    target_fraction = min(
        1.0 - margin,
        max(margin, boundary_fraction + signed),
    )
    if (
        direction == "lower" and target_fraction >= boundary_fraction
    ) or (
        direction == "upper" and target_fraction <= boundary_fraction
    ):
        return None
    value = discovery.value_at_fraction(name, target_fraction, source_bounds)
    return value, {
        "parameter": name,
        "direction": direction,
        "step_multiplier": multiplier,
        "source_grid_step": grid_step,
        "formal_boundary_transformed_fraction": boundary_fraction,
        "source_transformed_fraction": target_fraction,
    }


def outside_details(
    params: Mapping[str, Any],
    formal_ranges: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    result = {}
    for name, bounds in formal_ranges.items():
        value = float(params[name])
        lower = float(bounds["min_value"])
        upper = float(bounds["max_value"])
        if value < lower:
            result[name] = {
                "direction": "lower",
                "value": value,
                "formal_bound": lower,
            }
        elif value > upper:
            result[name] = {
                "direction": "upper",
                "value": value,
                "formal_bound": upper,
            }
    return result


def oat_samples(
    center: Mapping[str, Any],
    source_ranges: Mapping[str, Any],
    formal_ranges: Mapping[str, Any],
) -> Iterable[Tuple[str, Dict[str, float], Dict[str, Any]]]:
    for name in sorted(source_ranges):
        for direction in ("lower", "upper"):
            for multiplier in STEP_MULTIPLIERS:
                expanded = transformed_value(
                    name,
                    direction,
                    multiplier,
                    source_ranges[name],
                    formal_ranges[name],
                )
                if expanded is None:
                    continue
                value, component = expanded
                params = {key: float(item) for key, item in center.items()}
                params[name] = value
                details = outside_details(params, formal_ranges)
                if set(details) != {name}:
                    raise RuntimeError("OAT expansion is not one-parameter")
                yield (
                    f"oat_{name}_{direction}_step{multiplier}",
                    params,
                    {
                        "phase": "oat",
                        "components": [component],
                        "outside_formal_range_details": details,
                    },
                )


def pairwise_samples(
    center: Mapping[str, Any],
    source_ranges: Mapping[str, Any],
    formal_ranges: Mapping[str, Any],
) -> Iterable[Tuple[str, Dict[str, float], Dict[str, Any]]]:
    names = sorted(source_ranges)
    pair_index = 0
    for left_index, left in enumerate(names):
        for right in names[left_index + 1:]:
            pair_index += 1
            for multiplier_index, multiplier in enumerate(STEP_MULTIPLIERS):
                for orientation in (0, 1):
                    left_direction = (
                        "lower"
                        if (pair_index + multiplier_index + orientation) % 2
                        else "upper"
                    )
                    right_direction = (
                        "upper" if left_direction == "lower" else "lower"
                    )
                    left_expanded = transformed_value(
                        left,
                        left_direction,
                        multiplier,
                        source_ranges[left],
                        formal_ranges[left],
                    )
                    right_expanded = transformed_value(
                        right,
                        right_direction,
                        multiplier,
                        source_ranges[right],
                        formal_ranges[right],
                    )
                    if left_expanded is None or right_expanded is None:
                        continue
                    params = {
                        key: float(item) for key, item in center.items()
                    }
                    params[left] = left_expanded[0]
                    params[right] = right_expanded[0]
                    details = outside_details(params, formal_ranges)
                    if set(details) != {left, right}:
                        raise RuntimeError("pairwise expansion is not two-parameter")
                    yield (
                        (
                            f"pair_{left}_{left_direction}_{right}_"
                            f"{right_direction}_step{multiplier}"
                        ),
                        params,
                        {
                            "phase": "pairwise",
                            "components": [
                                left_expanded[1],
                                right_expanded[1],
                            ],
                            "outside_formal_range_details": details,
                        },
                    )


def interval(
    predicted_resource: Mapping[str, float],
    regime: Optional[Mapping[str, Any]],
) -> Dict[str, Dict[str, float]]:
    spans = (
        regime["relative_resource_span"]
        if regime is not None
        else {name: 1.0 for name in RESOURCES}
    )
    return {
        name: {
            "lower": max(
                0.0,
                float(predicted_resource[name]) * (1.0 - float(spans[name])),
            ),
            "median": float(predicted_resource[name]),
            "upper": float(predicted_resource[name]) * (1.0 + float(spans[name])),
            "relative_span": float(spans[name]),
        }
        for name in RESOURCES
    }


def select_top5(
    candidates: Sequence[Dict[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    trusted_pool = sorted(
        (item for item in candidates if item["trusted"]),
        key=lambda item: (
            -float(item["robust_regime_ranking_score"]),
            -float(item["candidate_score"]),
            item["source_provenance"],
        ),
    )
    trusted = trusted_pool[:3]
    if len(trusted) != 3:
        return None
    trusted.sort(
        key=lambda item: (
            -float(item["candidate_score"]),
            item["source_provenance"],
        )
    )

    probe_pool = sorted(
        (item for item in candidates if not item["trusted"]),
        key=lambda item: (
            item["exact_plan_regime_validated"],
            -float(item["candidate_score"]),
            item["source_provenance"],
        ),
    )
    probes: List[Dict[str, Any]] = []
    for item in probe_pool:
        if item["three_level_fingerprint"]["id"] in {
            selected["three_level_fingerprint"]["id"]
            for selected in [*trusted, *probes]
        }:
            continue
        probes.append(item)
        if len(probes) == 2:
            break
    if len(probes) != 2:
        return None
    return [*trusted, *probes]


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    if common.sha256(FORMAL_STATE) != FORMAL_STATE_SHA:
        raise RuntimeError("formal state SHA changed before wide expansion")
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
    regimes, historical_ids, historical_params, source_manifest = (
        discovery.historical_context()
    )

    ranking = common.load_ranking()
    source_anchor = ranking["candidate_rankings"][0]
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
        source_anchor["initial_params"],
        source_anchor["initial_resource"],
    )
    center = common.clipped_non_corner(
        state["incumbent_params"],
        source_ranges,
    )
    candidates_by_id: Dict[str, Dict[str, Any]] = {}
    seen_params = set(historical_params)
    errors: List[Dict[str, str]] = []
    phase_counts = {"oat": 0, "pairwise": 0}

    def evaluate(
        sample: Tuple[str, Dict[str, float], Dict[str, Any]],
    ) -> None:
        provenance, params, expansion = sample
        params_id = robust.parameter_key(params)
        if params_id in seen_params:
            return
        seen_params.add(params_id)
        phase_counts[expansion["phase"]] += 1
        try:
            raw_resource, _stats, metadata = estimator.estimate_with_metadata(
                queries,
                params,
            )
        except Exception as exc:
            errors.append(
                {
                    "source_provenance": provenance,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return
        plan = str(metadata.get("workload_plan_fingerprint") or "")
        if not plan.startswith("ratio-plan-sha256:"):
            errors.append(
                {
                    "source_provenance": provenance,
                    "error": "missing workload plan fingerprint",
                }
            )
            return
        feature_ratio = {
            name: float(metadata["feature_ratios"][name])
            for name in RESOURCES
        }
        regime = regimes.get(plan)
        trusted = bool(regime and regime["trusted_eligible"])
        scalars = (
            regime["resource_scalar_median"]
            if regime is not None
            else {name: 1.0 for name in RESOURCES}
        )
        predicted_resource = {
            name: float(raw_resource[name]) * float(scalars[name])
            for name in RESOURCES
        }
        fingerprint = common.fingerprint(
            plan,
            feature_ratio,
            predicted_resource,
        )
        if fingerprint["id"] in historical_ids:
            return
        score = common.score(predicted_resource)
        relative_spans = (
            regime["relative_resource_span"]
            if regime is not None
            else {name: 1.0 for name in RESOURCES}
        )
        uncertainty_penalty = min(
            1.0,
            0.25 * sum(float(relative_spans[name]) for name in RESOURCES),
        )
        empirical_score = (
            float(regime["actual_score_median"])
            if regime is not None
            else score
        )
        candidate = copy.deepcopy(source_anchor)
        outside = expansion["outside_formal_range_details"]
        candidate.update(
            {
                "source_provenance": provenance,
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
                "predicted_resource_interval": interval(
                    predicted_resource,
                    regime,
                ),
                "final_resource": predicted_resource,
                "utilization": {
                    name: predicted_resource[name] / common.CAPABILITY[name]
                    for name in RESOURCES
                },
                "candidate_score": score,
                "predicted_score": score,
                "final_score": score,
                "robust_regime_ranking_score": (
                    empirical_score - uncertainty_penalty
                ),
                "calibration_uncertainty_penalty": uncertainty_penalty,
                "delta_param": common.delta_param(
                    state["incumbent_params"],
                    params,
                ),
                "eta": 0.0,
                "workload_plan_fingerprint": plan,
                "three_level_fingerprint": fingerprint,
                "boundary_plateau": False,
                "boundary_corner": not common.is_non_corner(
                    params,
                    source_ranges,
                ),
                "outside_formal_range": True,
                "outside_formal_range_details": outside,
                "wide_range_expansion_probe": expansion,
                "probe_kind": (
                    "wide_expansion_exact_regime_candidate"
                    if trusted
                    else "wide_expansion_unknown_or_unstable_probe"
                ),
                "incumbent_update_eligible": False,
                "diagnostic_only": True,
                "qps_used_for_inner_scoring": False,
                "plan_regime_calibration": {
                    "mode": (
                        "exact_regime_componentwise_median_with_interval"
                        if regime is not None
                        else "unknown_plan_regime_probe_only"
                    ),
                    "trusted_eligible": trusted,
                    "regime": copy.deepcopy(regime),
                },
            }
        )
        if (
            candidate["boundary_corner"]
            or not outside
            or not all(
                float(bounds["min_value"])
                < float(params[name])
                < float(bounds["max_value"])
                for name, bounds in source_ranges.items()
            )
        ):
            raise RuntimeError("wide expansion candidate boundary failure")
        current = candidates_by_id.get(fingerprint["id"])
        if current is None or (
            trusted,
            float(candidate["robust_regime_ranking_score"]),
            float(candidate["candidate_score"]),
            provenance,
        ) > (
            current["trusted"],
            float(current["robust_regime_ranking_score"]),
            float(current["candidate_score"]),
            current["source_provenance"],
        ):
            candidates_by_id[fingerprint["id"]] = candidate

    for sample in oat_samples(center, source_ranges, formal_ranges):
        evaluate(sample)
    top5 = select_top5(list(candidates_by_id.values()))
    stopped_after = "oat"
    if top5 is None:
        stopped_after = "pairwise"
        for sample in pairwise_samples(center, source_ranges, formal_ranges):
            evaluate(sample)
            top5 = select_top5(list(candidates_by_id.values()))
            if top5 is not None:
                break

    if top5 is None:
        trusted_count = sum(
            item["trusted"] for item in candidates_by_id.values()
        )
        evidence = {
            "schema_version": "tpcc_batch06_prep_wide_expansion_failure_v1",
            "formal_state_sha256": FORMAL_STATE_SHA,
            "formal_state_or_range_mutated": False,
            "step_multipliers": list(STEP_MULTIPLIERS),
            "phase_counts": phase_counts,
            "novel_fingerprint_count": len(candidates_by_id),
            "trusted_fingerprint_count": trusted_count,
            "probe_fingerprint_count": len(candidates_by_id) - trusted_count,
            "explain_error_count": len(errors),
            "explain_errors": errors,
        }
        FAIL_EVIDENCE.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(
            "wide expansion cannot form trusted3+probe2; "
            f"evidence={FAIL_EVIDENCE}"
        )

    for rank, candidate in enumerate(top5, 1):
        candidate["rank"] = rank
    roles = [item["selection_role"] for item in top5]
    ids = [item["three_level_fingerprint"]["id"] for item in top5]
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
    if (
        roles
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or len(set(ids)) != 5
        or historical_ids.intersection(ids)
        or any(
            item["boundary_corner"]
            or item.get("outside_formal_range") is not True
            or any(item.get(field) is None for field in required_fields)
            for item in top5
        )
    ):
        raise RuntimeError("wide expansion Top-5 contract failed")

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
        "source_generation_only_stop_line_number": 0,
        "source_config": str(common.CONFIG),
        "source_config_sha256": common.sha256(common.CONFIG),
        "source_discovery": str(robust.SOURCES[0][0]),
        "source_discovery_sha256": common.sha256(robust.SOURCES[0][0]),
        "source_discovery_ledger": str(robust.SOURCES[0][1]),
        "source_discovery_ledger_sha256": common.sha256(robust.SOURCES[0][1]),
        "formal_range_state_source": str(FORMAL_STATE),
        "formal_range_state_source_sha256": FORMAL_STATE_SHA,
        "formal_candidate_param_ranges": formal_ranges,
        "immutable_source_param_ranges": source_ranges,
        "source_batches": source_manifest,
        "robust_regimes": regimes,
        "wide_expansion_contract": {
            "kind": "oat_then_deterministic_pairwise_transformed_grid_v1",
            "formal_state_or_range_update": False,
            "step_multipliers": list(STEP_MULTIPLIERS),
            "stopped_after_phase": stopped_after,
            "phase_counts": phase_counts,
            "all_points_outside_formal_range": True,
            "all_points_inside_immutable_source_noncorner": True,
            "fingerprint_quantum": 0.005,
        },
        "candidate_pool_size": len(candidates_by_id),
        "explain_error_count": len(errors),
        "explain_errors": errors,
        "top5": top5,
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
                "roles": roles,
                "fingerprints": ids,
                "plans": [
                    item["workload_plan_fingerprint"] for item in top5
                ],
                "phase_counts": phase_counts,
                "stopped_after_phase": stopped_after,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
