#!/usr/bin/env python3
"""Build TPC-C batch06-prep Top-5 from source-interior Halton probes."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, Mapping, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_batch06_prep_wide_expansion_top5 as wide


discovery = wide.discovery
robust = wide.robust
common = wide.common
FORMAL_STATE = wide.FORMAL_STATE
FORMAL_STATE_SHA = wide.FORMAL_STATE_SHA
OUTPUT = Path("/tmp/tpcc_batch06_prep_source_halton_top5_v1.json")
FAIL_EVIDENCE = Path(
    "/tmp/tpcc_batch06_prep_source_halton_failure_evidence_v1.json"
)
HALTON_BASES = (2, 3, 5, 7, 11, 13, 17)
MAX_EXPLAIN_POINTS = 256
RESOURCES = common.RESOURCES


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


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


def halton_samples(
    source_ranges: Mapping[str, Any],
) -> Iterable[Tuple[int, Dict[str, float], Tuple[float, ...]]]:
    names = tuple(sorted(source_ranges))
    margin = float(common.NON_CORNER_MARGIN) + 1e-6
    usable = 1.0 - 2.0 * margin
    for sample_index in range(1, MAX_EXPLAIN_POINTS + 1):
        params = {}
        coordinates = []
        for dimension, name in enumerate(names):
            raw = discovery.van_der_corput(
                sample_index + 37 * dimension,
                HALTON_BASES[dimension],
            )
            fraction = margin + usable * raw
            coordinates.append(fraction)
            params[name] = discovery.value_at_fraction(
                name,
                fraction,
                source_ranges[name],
            )
        yield sample_index, params, tuple(coordinates)


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    if common.sha256(FORMAL_STATE) != FORMAL_STATE_SHA:
        raise RuntimeError("formal state changed before Halton discovery")
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

    candidates_by_id: Dict[str, Dict[str, Any]] = {}
    seen_params = set(historical_params)
    errors = []
    explain_point_count = 0
    top5 = None
    stopped_at_sample_index = None
    for sample_index, params, coordinates in halton_samples(source_ranges):
        params_id = robust.parameter_key(params)
        if params_id in seen_params:
            continue
        seen_params.add(params_id)
        explain_point_count += 1
        try:
            raw_resource, _stats, metadata = estimator.estimate_with_metadata(
                queries,
                params,
            )
        except Exception as exc:
            errors.append(
                {
                    "sample_index": sample_index,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        plan = str(metadata.get("workload_plan_fingerprint") or "")
        if not plan.startswith("ratio-plan-sha256:"):
            errors.append(
                {
                    "sample_index": sample_index,
                    "error": "missing workload plan fingerprint",
                }
            )
            continue
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
            continue
        score = common.score(predicted_resource)
        spans = (
            regime["relative_resource_span"]
            if regime is not None
            else {name: 1.0 for name in RESOURCES}
        )
        uncertainty_penalty = min(
            1.0,
            0.25 * sum(float(spans[name]) for name in RESOURCES),
        )
        empirical_score = (
            float(regime["actual_score_median"])
            if regime is not None
            else score
        )
        outside = outside_details(params, formal_ranges)
        candidate = copy.deepcopy(source_anchor)
        candidate.update(
            {
                "source_provenance": f"source_halton_{sample_index:03d}",
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
                "predicted_resource_interval": wide.interval(
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
                "outside_formal_range": bool(outside),
                "outside_formal_range_details": outside,
                "source_halton_probe": {
                    "sample_index": sample_index,
                    "bases": list(HALTON_BASES),
                    "source_transformed_coordinates": list(coordinates),
                    "source_noncorner_margin": common.NON_CORNER_MARGIN,
                },
                "probe_kind": (
                    "source_halton_exact_regime_candidate"
                    if trusted
                    else "source_halton_unknown_or_unstable_probe"
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
            or not common.is_non_corner(params, source_ranges)
        ):
            raise RuntimeError("Halton candidate is not source-noncorner")
        current = candidates_by_id.get(fingerprint["id"])
        if current is None or (
            trusted,
            float(candidate["robust_regime_ranking_score"]),
            float(candidate["candidate_score"]),
            candidate["source_provenance"],
        ) > (
            current["trusted"],
            float(current["robust_regime_ranking_score"]),
            float(current["candidate_score"]),
            current["source_provenance"],
        ):
            candidates_by_id[fingerprint["id"]] = candidate
        top5 = wide.select_top5(list(candidates_by_id.values()))
        if top5 is not None:
            stopped_at_sample_index = sample_index
            break

    if top5 is None:
        trusted_count = sum(
            item["trusted"] for item in candidates_by_id.values()
        )
        evidence = {
            "schema_version": "tpcc_batch06_prep_source_halton_failure_v1",
            "formal_state_sha256": FORMAL_STATE_SHA,
            "formal_state_or_range_mutated": False,
            "max_explain_points": MAX_EXPLAIN_POINTS,
            "explain_point_count": explain_point_count,
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
            "source Halton cannot form trusted3+probe2; "
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
            or any(item.get(field) is None for field in required_fields)
            for item in top5
        )
    ):
        raise RuntimeError("source Halton Top-5 contract failed")

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
        "source_halton_contract": {
            "kind": "deterministic_source_interior_halton_stop_on_top5_v1",
            "formal_state_or_range_update": False,
            "bases": list(HALTON_BASES),
            "maximum_explain_points": MAX_EXPLAIN_POINTS,
            "actual_explain_points": explain_point_count,
            "stopped_at_sample_index": stopped_at_sample_index,
            "source_noncorner_margin": common.NON_CORNER_MARGIN,
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
                "actual_explain_points": explain_point_count,
                "stopped_at_sample_index": stopped_at_sample_index,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
