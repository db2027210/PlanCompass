#!/usr/bin/env python3
"""Build five novel, non-corner TPC-C regime probes for diagnostic replay."""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_round13_plan_regime_corrected_top5 as common


OUTPUT = Path("/tmp/tpcc_noncorner_regime_discovery_top5.json")
KNOWN_SELECTIONS = (
    common.ROUND12 / "top5.json",
    common.ATTEMPT
    / "formal10_tpcc_batch01_after_entry_20260729T1554"
    / "top5.json",
)
HALTON_BASES = (2, 3, 5, 7, 11, 13, 17)
DESIGN_SIZE = 96
DESIGN_MARGIN = 0.08


def radical_inverse(index: int, base: int) -> float:
    value = 0.0
    factor = 1.0 / base
    while index:
        index, digit = divmod(index, base)
        value += digit * factor
        factor /= base
    return value


def point_from_fractions(
    fractions: Sequence[float],
    ranges: Mapping[str, Any],
) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for (name, bounds), fraction in zip(sorted(ranges.items()), fractions):
        fraction = DESIGN_MARGIN + (1.0 - 2.0 * DESIGN_MARGIN) * fraction
        lower = float(bounds["min_value"])
        upper = float(bounds["max_value"])
        if name in common.LOG_TRANSFORM_PARAMS:
            result[name] = math.exp(
                math.log(lower) + fraction * (math.log(upper) - math.log(lower))
            )
        else:
            result[name] = lower + fraction * (upper - lower)
    if not common.is_non_corner(result, ranges):
        raise RuntimeError("generated design point is a corner")
    return result


def normalized_vector(
    params: Mapping[str, Any],
    ranges: Mapping[str, Any],
) -> Tuple[float, ...]:
    return tuple(
        common.transformed_fraction(name, float(params[name]), bounds)
        for name, bounds in sorted(ranges.items())
    )


def squared_distance(left: Sequence[float], right: Sequence[float]) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right))


def known_fingerprint_ids() -> set[str]:
    result: set[str] = set()
    for path in KNOWN_SELECTIONS:
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for candidate in data.get("top5") or []:
            fingerprint = candidate.get("three_level_fingerprint") or {}
            fingerprint_id = str(fingerprint.get("id") or "")
            if fingerprint_id:
                result.add(fingerprint_id)
    return result


def select_plan_distinct_maximin(
    candidates: List[Dict[str, Any]],
    ranges: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    by_plan: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        by_plan.setdefault(candidate["workload_plan_fingerprint"], []).append(candidate)
    if len(by_plan) < 5:
        raise RuntimeError(
            f"need at least five non-corner plan regimes, found {len(by_plan)}"
        )
    plan_representatives = [
        max(items, key=lambda item: float(item["candidate_score"]))
        for items in by_plan.values()
    ]
    plan_representatives.sort(
        key=lambda item: (
            -float(item["candidate_score"]),
            item["three_level_fingerprint"]["id"],
        )
    )
    selected = [plan_representatives.pop(0)]
    while len(selected) < 5:
        selected_vectors = [
            normalized_vector(item["optimized_params"], ranges) for item in selected
        ]

        def key(candidate: Mapping[str, Any]) -> Tuple[float, float, str]:
            vector = normalized_vector(candidate["optimized_params"], ranges)
            minimum_distance = min(
                squared_distance(vector, selected_vector)
                for selected_vector in selected_vectors
            )
            return (
                minimum_distance,
                float(candidate["candidate_score"]),
                str(candidate["three_level_fingerprint"]["id"]),
            )

        chosen = max(plan_representatives, key=key)
        selected.append(chosen)
        plan_representatives.remove(chosen)
    return selected


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    config = json.loads(common.CONFIG.read_text(encoding="utf-8"))
    ranking = common.load_ranking()
    source_anchor = ranking["candidate_rankings"][0]
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

    excluded_ids = known_fingerprint_ids()
    candidates: List[Dict[str, Any]] = []
    seen_ids = set(excluded_ids)
    seen_params: set[str] = set()
    dimensions = len(config["param_ranges"])
    if dimensions != len(HALTON_BASES):
        raise RuntimeError("Halton design dimension mismatch")

    for index in range(1, DESIGN_SIZE + 1):
        fractions = [
            radical_inverse(index, base) for base in HALTON_BASES[:dimensions]
        ]
        params = point_from_fractions(fractions, config["param_ranges"])
        param_id = common.canonical_sha256(params)
        if param_id in seen_params:
            continue
        seen_params.add(param_id)
        raw_resource, _stats, metadata = estimator.estimate_with_metadata(
            queries, params
        )
        plan = str(metadata.get("workload_plan_fingerprint") or "")
        if not plan.startswith("ratio-plan-sha256:"):
            raise RuntimeError(f"design point {index} lacks a plan fingerprint")
        feature_ratio = {
            name: float(metadata["feature_ratios"][name])
            for name in common.RESOURCES
        }
        predicted_resource = {
            name: float(raw_resource[name]) for name in common.RESOURCES
        }
        fingerprint = common.fingerprint(
            plan, feature_ratio, predicted_resource
        )
        if fingerprint["id"] in seen_ids:
            continue
        seen_ids.add(fingerprint["id"])
        candidate = copy.deepcopy(source_anchor)
        candidate.update(
            {
                "source_provenance": f"noncorner_halton_{index:03d}",
                "selection_role": "uncertain_probe",
                "trusted": False,
                "uncertain_probe": True,
                "exact_plan_regime_validated": False,
                "unknown_regime_policy": "probe_only_no_state_updates",
                "optimized_params": params,
                "params": params,
                "feature_ratio": feature_ratio,
                "uncalibrated_predicted_resource": predicted_resource,
                "predicted_resource": predicted_resource,
                "final_resource": predicted_resource,
                "utilization": {
                    name: predicted_resource[name] / common.CAPABILITY[name]
                    for name in common.RESOURCES
                },
                "candidate_score": common.score(predicted_resource),
                "predicted_score": common.score(predicted_resource),
                "final_score": common.score(predicted_resource),
                "delta_param": common.delta_param(baseline_params, params),
                "workload_plan_fingerprint": plan,
                "three_level_fingerprint": fingerprint,
                "boundary_plateau": False,
                "boundary_corner": False,
                "probe_kind": "non_corner_plan_regime_discovery",
                "incumbent_update_eligible": False,
                "diagnostic_only": True,
                "qps_used_for_inner_scoring": False,
                "plan_regime_calibration": {
                    "mode": "unknown_plan_regime_probe_only",
                    "resource_scalar": None,
                },
            }
        )
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
                raise RuntimeError(
                    f"candidate missing required field: {required}"
                )
        candidates.append(candidate)

    top5 = select_plan_distinct_maximin(candidates, config["param_ranges"])
    for rank, candidate in enumerate(top5, 1):
        candidate["rank"] = rank
        if (
            candidate["boundary_corner"]
            or not common.is_non_corner(
                candidate["optimized_params"], config["param_ranges"]
            )
        ):
            raise RuntimeError(f"rank {rank} violates non-corner contract")

    output = {
        "schema_version": 3,
        "selection_contract": "tpcc_round12_current_plan_discovery_probe5_v1",
        "selection_revision": "noncorner_margin05_plan_distinct_maximin_v1",
        "diagnostic_only": True,
        "state_mutation_contract": {
            "accepted_update": False,
            "incumbent_update": False,
            "range_update": False,
            "gradient_update": False,
        },
        "qps_role": "outer_audit_only_not_inner_score",
        "maximum_real_workload_invocations": 5,
        "non_corner_margin": common.NON_CORNER_MARGIN,
        "design_margin": DESIGN_MARGIN,
        "source_history": str(common.HISTORY),
        "source_history_sha256": common.sha256(common.HISTORY),
        "source_ranking_line_number": ranking["_source_line_number"],
        "source_generation_only_stop_line_number": None,
        "source_config": str(common.CONFIG),
        "source_config_sha256": common.sha256(common.CONFIG),
        "candidate_pool_size": len(candidates),
        "unique_plan_regime_count": len(
            {item["workload_plan_fingerprint"] for item in candidates}
        ),
        "excluded_historical_fingerprint_ids": sorted(excluded_ids),
        "top5": top5,
    }
    OUTPUT.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(OUTPUT),
                "sha256": common.sha256(OUTPUT),
                "candidate_pool_size": len(candidates),
                "unique_plan_regime_count": output["unique_plan_regime_count"],
                "fingerprints": [
                    item["three_level_fingerprint"]["id"] for item in top5
                ],
                "plans": [
                    item["workload_plan_fingerprint"] for item in top5
                ],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
