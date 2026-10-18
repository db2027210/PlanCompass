#!/usr/bin/env python3
"""Build an EXPLAIN-only, range-bound TPC-C batch06-prep discovery Top-5.

The output contains five diagnostic-only uncertain probes from plan regimes
that have not appeared in any clean evidence through formal batch05.  This
script performs PostgreSQL EXPLAIN estimation but never invokes the workload.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_formal_batch06_novel_top5 as batch06


robust = batch06.robust
common = robust.common
ATTEMPT = robust.ATTEMPT
FORMAL_STATE = ATTEMPT / "formal10_tpcc_state.json"
EXPECTED_FORMAL_STATE_SHA256 = (
    "4a273208d8503f2d18a108293bf5714043418bdf72766bb44e8e10f16e5f9d5f"
)
OUTPUT = Path(
    "/tmp/tpcc_batch06_prep_rangebound_discovery_top5_v2_adaptive_level.json"
)
FAIL_EVIDENCE = Path(
    "/tmp/tpcc_batch06_prep_rangebound_discovery_failure_evidence_v2.json"
)
GRID_LEVELS = (0.10, 0.25, 0.50, 0.75, 0.90)
HALTON_BASES = (2, 3, 5, 7, 11, 13, 17)
HALTON_SAMPLE_COUNT = 96
LEVEL_QUANTA = (
    0.005,
    0.0025,
    0.001,
    0.0005,
    0.00025,
    0.0001,
    0.00005,
    0.00001,
)
RESOURCES = common.RESOURCES


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def van_der_corput(index: int, base: int) -> float:
    result = 0.0
    denominator = 1.0
    while index:
        index, remainder = divmod(index, base)
        denominator *= base
        result += remainder / denominator
    return result


def value_at_fraction(
    name: str,
    fraction: float,
    bounds: Mapping[str, Any],
) -> float:
    lower = float(bounds["min_value"])
    upper = float(bounds["max_value"])
    if not 0.0 < fraction < 1.0 or not lower < upper:
        raise RuntimeError(f"invalid sample fraction/range: {name}")
    if name in common.LOG_TRANSFORM_PARAMS:
        return math.exp(
            math.log(lower) + fraction * (math.log(upper) - math.log(lower))
        )
    return lower + fraction * (upper - lower)


def normalized_coordinates(
    params: Mapping[str, Any],
    ranges: Mapping[str, Any],
    names: Sequence[str],
) -> Tuple[float, ...]:
    return tuple(
        common.transformed_fraction(name, float(params[name]), ranges[name])
        for name in names
    )


def euclidean(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(
        sum((float(a) - float(b)) ** 2 for a, b in zip(left, right))
    )


def adaptive_fingerprint(
    plan: str,
    feature_ratio: Mapping[str, Any],
    resource: Mapping[str, Any],
    quantum: float,
) -> Dict[str, Any]:
    utilization = {
        name: float(resource[name]) / common.CAPABILITY[name]
        for name in RESOURCES
    }
    feature_level = common.quantize(feature_ratio, quantum)
    resource_level = common.quantize(utilization, quantum)
    payload = {
        "contract": "plan_feature_resource_three_level_adaptive_resolution_v2",
        "level_quantum": quantum,
        "plan": plan,
        "feature_ratio_level": feature_level,
        "predicted_resource_level": resource_level,
    }
    return {
        "contract": "plan_feature_resource_three_level_adaptive_resolution_v2",
        "id": "three-level-sha256:" + common.canonical_sha256(payload),
        "valid": True,
        "validation_errors": [],
        "plan_level": plan,
        "feature_ratio_level": feature_level,
        "predicted_resource_level": resource_level,
        "feature_ratio_quantum": quantum,
        "predicted_resource_utilization_quantum": quantum,
        "parameter_fallback_used": False,
    }


def deterministic_samples(
    ranges: Mapping[str, Any],
    incumbent: Mapping[str, Any],
) -> Iterable[Tuple[str, Dict[str, float]]]:
    names = tuple(sorted(ranges))
    center = common.clipped_non_corner(incumbent, ranges)
    yield "formal_incumbent_center", center

    for name in names:
        for fraction in GRID_LEVELS:
            params = dict(center)
            params[name] = value_at_fraction(name, fraction, ranges[name])
            yield f"one_at_a_time_{name}_{fraction:.2f}", params

    margin = float(common.NON_CORNER_MARGIN) + 1e-6
    usable = 1.0 - 2.0 * margin
    for sample_index in range(1, HALTON_SAMPLE_COUNT + 1):
        params = {}
        for dimension, name in enumerate(names):
            raw = van_der_corput(
                sample_index + 19 * dimension,
                HALTON_BASES[dimension],
            )
            fraction = margin + usable * raw
            params[name] = value_at_fraction(name, fraction, ranges[name])
        yield f"halton_{sample_index:03d}", params


def historical_context() -> Tuple[
    Dict[str, Dict[str, Any]],
    set[str],
    set[str],
    List[Dict[str, Any]],
]:
    batches = []
    historical_ids: set[str] = set()
    historical_params: set[str] = set()
    source_manifest = []
    for selection_path, ledger_path, source_name in robust.SOURCES:
        selected, successful = robust.load_clean_batch(
            selection_path,
            ledger_path,
            source_name,
        )
        batches.append((selected, successful, source_name))
        historical_ids.update(
            item["three_level_fingerprint"]["id"] for item in selected
        )
        historical_params.update(
            robust.parameter_key(item["optimized_params"]) for item in selected
        )
        source_manifest.append(
            {
                "name": source_name,
                "selection_path": str(selection_path),
                "selection_sha256": common.sha256(selection_path),
                "ledger_path": str(ledger_path),
                "ledger_sha256": common.sha256(ledger_path),
            }
        )
    return (
        robust.robust_regimes(batches),
        historical_ids,
        historical_params,
        source_manifest,
    )


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    if common.sha256(FORMAL_STATE) != EXPECTED_FORMAL_STATE_SHA256:
        raise RuntimeError("formal state SHA changed before discovery generation")
    state = load_json(FORMAL_STATE)
    if (
        state.get("status") != "ready"
        or state.get("accepted_count") != 1
        or state.get("accepted_target") != 10
        or state.get("formal_physical_call_count") != 25
        or state.get("formal_valid_replay_count") != 25
        or state.get("formal_technical_failure_count") != 0
        or state.get("qps_used_for_inner_scoring") is not False
    ):
        raise RuntimeError("formal state is not clean post-batch05 state")
    ranges = copy.deepcopy(state["range_state"])
    names = tuple(sorted(ranges))
    regimes, historical_ids, historical_params, source_manifest = (
        historical_context()
    )

    config = load_json(common.CONFIG)
    ranking = common.load_ranking()
    source_anchor = ranking["candidate_rankings"][0]
    baseline_params = {
        name: float(value)
        for name, value in source_anchor["initial_params"].items()
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

    candidates: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    seen_params = set(historical_params)
    for provenance, params in deterministic_samples(
        ranges,
        state["incumbent_params"],
    ):
        params = common.clipped_non_corner(params, ranges)
        params_id = robust.parameter_key(params)
        if params_id in seen_params:
            continue
        seen_params.add(params_id)
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
            continue
        plan = str(metadata.get("workload_plan_fingerprint") or "")
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
            name: float(metadata["feature_ratios"][name])
            for name in RESOURCES
        }
        predicted_resource = {
            name: float(raw_resource[name]) for name in RESOURCES
        }
        candidate_score = common.score(predicted_resource)
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
                "candidate_score": candidate_score,
                "predicted_score": candidate_score,
                "final_score": candidate_score,
                "delta_param": common.delta_param(
                    state["incumbent_params"],
                    params,
                ),
                "eta": 0.0,
                "workload_plan_fingerprint": plan,
                "boundary_plateau": False,
                "boundary_corner": not common.is_non_corner(params, ranges),
                "probe_kind": "rangebound_unknown_plan_regime_discovery",
                "incumbent_update_eligible": False,
                "diagnostic_only": True,
                "qps_used_for_inner_scoring": False,
                "plan_regime_calibration": {
                    "mode": "unknown_plan_regime_probe_only",
                    "trusted_eligible": False,
                    "regime": None,
                },
                "_normalized_coordinates": normalized_coordinates(
                    params,
                    ranges,
                    names,
                ),
            }
        )
        if candidate["boundary_corner"]:
            raise RuntimeError("range-bound discovery produced a corner")
        candidates.append(candidate)

    counts_by_quantum: Dict[str, int] = {}
    selected_quantum = None
    for quantum in LEVEL_QUANTA:
        ids = {
            adaptive_fingerprint(
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

    exact_signatures = {
        (
            item["workload_plan_fingerprint"],
            tuple(float(item["feature_ratio"][name]).hex() for name in RESOURCES),
            tuple(
                (
                    float(item["predicted_resource"][name])
                    / common.CAPABILITY[name]
                ).hex()
                for name in RESOURCES
            ),
        )
        for item in candidates
    }
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
    failure_evidence = {
        "schema_version": (
            "tpcc_batch06_prep_rangebound_discovery_resolution_evidence_v2"
        ),
        "formal_state_path": str(FORMAL_STATE),
        "formal_state_sha256": EXPECTED_FORMAL_STATE_SHA256,
        "formal_range_unchanged": True,
        "sample_contract": {
            "grid_levels": list(GRID_LEVELS),
            "halton_bases": list(HALTON_BASES),
            "halton_sample_count": HALTON_SAMPLE_COUNT,
        },
        "raw_unknown_estimate_count": len(candidates),
        "unknown_plan_count": len({
            item["workload_plan_fingerprint"] for item in candidates
        }),
        "exact_continuous_signature_count": len(exact_signatures),
        "fingerprint_count_by_global_level_quantum": counts_by_quantum,
        "response_ranges": response_ranges,
        "explain_error_count": len(errors),
        "explain_errors": errors,
        "minimum_nonmutating_next_action_if_insufficient": {
            "kind": "explain_only_range_expansion_probe",
            "formal_range_mutation": False,
            "proposal": (
                "expand one transformed grid step toward the immutable source "
                "config bounds, search one parameter direction at a time, and "
                "only propose a formal range change after five real distinct "
                "three-level fingerprints are observed"
            ),
        },
    }
    if selected_quantum is None:
        FAIL_EVIDENCE.write_text(
            json.dumps(failure_evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(
            "formal range has fewer than five genuine continuous three-level "
            f"responses; evidence={FAIL_EVIDENCE}"
        )

    fingerprint_representatives: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        fingerprint = adaptive_fingerprint(
            candidate["workload_plan_fingerprint"],
            candidate["feature_ratio"],
            candidate["predicted_resource"],
            selected_quantum,
        )
        candidate["three_level_fingerprint"] = fingerprint
        fingerprint_id = fingerprint["id"]
        current = fingerprint_representatives.get(fingerprint_id)
        if current is None or (
            float(candidate["candidate_score"]),
            candidate["source_provenance"],
        ) > (
            float(current["candidate_score"]),
            current["source_provenance"],
        ):
            fingerprint_representatives[fingerprint_id] = candidate
    pool = list(fingerprint_representatives.values())

    selected = [
        max(
            pool,
            key=lambda item: (
                float(item["candidate_score"]),
                item["source_provenance"],
            ),
        )
    ]
    while len(selected) < 5:
        remaining = [
            item
            for item in pool
            if item["three_level_fingerprint"]["id"]
            not in {
                chosen["three_level_fingerprint"]["id"] for chosen in selected
            }
        ]
        selected.append(
            max(
                remaining,
                key=lambda item: (
                    min(
                        euclidean(
                            item["_normalized_coordinates"],
                            chosen["_normalized_coordinates"],
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
        candidate.pop("_normalized_coordinates", None)
    selected_ids = [
        item["three_level_fingerprint"]["id"] for item in selected
    ]
    selected_plans = [
        item["workload_plan_fingerprint"] for item in selected
    ]
    if (
        len(set(selected_ids)) != 5
        or historical_ids.intersection(selected_ids)
        or any(plan in regimes for plan in selected_plans)
        or any(
            robust.parameter_key(item["optimized_params"])
            in historical_params
            for item in selected
        )
    ):
        raise RuntimeError("range-bound discovery novelty gate failed")
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
        "candidate_param_ranges": ranges,
        "source_batches": source_manifest,
        "known_exact_plan_regime_count": len(regimes),
        "sampling_contract": {
            "kind": "formal_range_oat_grid_plus_deterministic_halton_v1",
            "grid_levels": list(GRID_LEVELS),
            "halton_bases": list(HALTON_BASES),
            "halton_sample_count": HALTON_SAMPLE_COUNT,
            "level_resolution_contract": (
                "coarsest_global_quantum_with_at_least_five_real_fingerprints"
            ),
            "level_quantum_candidates": list(LEVEL_QUANTA),
            "selected_global_level_quantum": selected_quantum,
            "distinct_unknown_three_level_fingerprint_required": 5,
            "selection": "predicted_first_then_parameter_space_maximin",
        },
        "explain_attempted_parameter_count": len(seen_params)
        - len(historical_params),
        "explain_error_count": len(errors),
        "explain_errors": errors,
        "raw_unknown_estimate_count": len(candidates),
        "exact_continuous_signature_count": len(exact_signatures),
        "fingerprint_count_by_global_level_quantum": counts_by_quantum,
        "unknown_candidate_fingerprint_count": len(pool),
        "unknown_candidate_plan_count": len({
            item["workload_plan_fingerprint"] for item in candidates
        }),
        "response_ranges": response_ranges,
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
                "unknown_candidate_fingerprint_count": len(pool),
                "selected_quantum": selected_quantum,
                "unknown_candidate_plan_count": len({
                    item["workload_plan_fingerprint"] for item in candidates
                }),
                "explain_error_count": len(errors),
                "plans": selected_plans,
                "fingerprints": selected_ids,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
