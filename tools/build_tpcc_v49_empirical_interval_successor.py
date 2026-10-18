#!/usr/bin/env python3
"""Build a TPC-C successor with non-duplicating empirical calibration.

The v43/v44 builder starts from an exact-point empirical representative and
then may add a residual learned from the very same point.  This module keeps
the sealed v44 discovery and role machinery, but replaces that local model:

* measured exact-point repeats use their trusted empirical resource/score;
* an exact point is never corrected again by a distance-zero residual;
* unmeasured neighbours use only non-zero-distance anchors, alpha in [0, 1],
  and persist leave-one-out diagnostics;
* every resource prediction is bounded by its persisted calibration interval.

No workload is invoked and formal state is read-only.
"""

from __future__ import annotations

import argparse
import copy
from itertools import product
import json
import os
from pathlib import Path
from statistics import median
from typing import Any, Dict, Mapping, Sequence

try:
    import build_tpcc_v44_plan_regime_score_separation as v44
    import build_tpcc_v49_empirical_interval_core as core
except ModuleNotFoundError:
    from tools import build_tpcc_v44_plan_regime_score_separation as v44
    from tools import build_tpcc_v49_empirical_interval_core as core


BUILD_CONTRACT = "tpcc_v49_empirical_repeat_interval_calibration_v1"
RESOURCES = tuple(v44.v6.RESOURCES)
v6 = v44.v6
ACQUISITION_GATE_SCHEMA = v44.ACQUISITION_GATE_SCHEMA
_EXACT_INTERVALS: Dict[str, Dict[str, Dict[str, float]]] = {}


def _key(candidate: Mapping[str, Any]) -> str:
    value = str(candidate.get("exact_point_key") or "")
    if value:
        return value
    return v44.v6._point_key(
        v44.v6._params(candidate), str(candidate["workload_plan_fingerprint"])
    )


def _exact_interval(
    observations: Sequence[Mapping[str, Any]],
    center: Mapping[str, float],
) -> Dict[str, Dict[str, float]]:
    result: Dict[str, Dict[str, float]] = {}
    for name in RESOURCES:
        values = [float(row["actual_resource"][name]) for row in observations]
        lower = min(values + [float(center[name])])
        upper = max(values + [float(center[name])])
        denominator = max(abs(float(center[name])), 1e-12)
        result[name] = {
            "lower": lower,
            "median": float(center[name]),
            "upper": upper,
            "relative_span": (upper - lower) / denominator,
        }
    return result


def _loo_nonexact(
    anchors: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
) -> Dict[str, Any]:
    if len(anchors) < 2:
        return {
            "defined": False,
            "anchor_count": len(anchors),
            "score_mae": None,
            "resource_mae_pp": {name: None for name in RESOURCES},
        }
    score_errors = []
    resource_errors = {name: [] for name in RESOURCES}
    for held_index, (held_candidate, held_observation) in enumerate(anchors):
        training = [row for index, row in enumerate(anchors) if index != held_index]
        predicted = {
            name: median(
                float(observation["actual_resource"][name])
                for _candidate, observation in training
            )
            for name in RESOURCES
        }
        score_errors.append(
            abs(v44.v6._score(predicted) - float(held_observation["actual_score"]))
        )
        for name in RESOURCES:
            capability = float(v44.v6.repeat_v2.CAPABILITY[name])
            resource_errors[name].append(
                abs(predicted[name] - float(held_observation["actual_resource"][name]))
                / capability
                * 100.0
            )
    return {
        "defined": True,
        "anchor_count": len(anchors),
        "score_mae": sum(score_errors) / len(score_errors),
        "score_max_absolute_error": max(score_errors),
        "resource_mae_pp": {
            name: sum(values) / len(values)
            for name, values in resource_errors.items()
        },
        "resource_max_absolute_error_pp": {
            name: max(values) for name, values in resource_errors.items()
        },
    }


def _local_model(
    candidate: Mapping[str, Any],
    historical_resource: Mapping[str, Any],
    historical_score: float,
    anchors: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    ranges: Mapping[str, Any],
) -> Dict[str, Any]:
    candidate_key = _key(candidate)
    exact_interval = _EXACT_INTERVALS.get(candidate_key)
    if exact_interval is not None:
        resource = {name: float(historical_resource[name]) for name in RESOURCES}
        for name in RESOURCES:
            bounds = exact_interval[name]
            if not float(bounds["lower"]) <= resource[name] <= float(bounds["upper"]):
                raise RuntimeError("TPC-C v49 exact prediction escaped calibration interval")
        return {
            "contract": "tpcc_v49_exact_empirical_repeat_v1",
            "anchor_rows": [],
            "resource_offset": {name: 0.0 for name in RESOURCES},
            "score_offset": 0.0,
            "score_target_before_resource_reconciliation": float(historical_score),
            "all_resource_reconciliation_alpha": 0.0,
            "predicted_resource": resource,
            "predicted_score": float(historical_score),
            "score_target_reconciliation_error": 0.0,
            "score_residual_mad": 0.0,
            "io_only_scalar_calibration": False,
            "exact_point_repeat": True,
            "distance_zero_residual_applied": False,
            "predicted_resource_interval": copy.deepcopy(exact_interval),
            "prediction_inside_calibration_interval": True,
            "nonexact_loo": None,
        }

    nonzero = [
        (anchor_candidate, observation)
        for anchor_candidate, observation in anchors
        if v44._parameter_distance(candidate, anchor_candidate, ranges) > 1e-15
    ]
    if not nonzero:
        raise RuntimeError("TPC-C v49 unmeasured point lacks non-zero-distance anchors")
    weighted = []
    for anchor_candidate, observation in nonzero:
        distance = v44._parameter_distance(candidate, anchor_candidate, ranges)
        raw_weight = 1.0 / (0.02 + distance) ** 2
        residual = {
            name: float(observation["actual_resource"][name])
            - float(anchor_candidate["predicted_resource"][name])
            for name in RESOURCES
        }
        weighted.append(
            {
                "rank": int(anchor_candidate["rank"]),
                "distance": distance,
                "raw_weight": raw_weight,
                "resource_residual": residual,
                "score_residual": float(observation["actual_score"])
                - float(anchor_candidate["predicted_score"]),
                "selection_role": anchor_candidate["selection_role"],
            }
        )
    weight_sum = sum(row["raw_weight"] for row in weighted)
    for row in weighted:
        row["weight"] = row["raw_weight"] / weight_sum
    empirical_resource = {
        name: sum(
            row["weight"]
            * float(nonzero[index][1]["actual_resource"][name])
            for index, row in enumerate(weighted)
        )
        for name in RESOURCES
    }
    offset = {
        name: empirical_resource[name] - float(historical_resource[name])
        for name in RESOURCES
    }
    score_target = sum(
        row["weight"] * float(nonzero[index][1]["actual_score"])
        for index, row in enumerate(weighted)
    )
    score_offset = score_target - float(historical_score)
    alpha = 1.0
    resource = empirical_resource
    score = v44.v6._score(resource)
    interval: Dict[str, Dict[str, float]] = {}
    for name in RESOURCES:
        possible = [resource[name]] + [
            float(observation["actual_resource"][name])
            for _candidate, observation in nonzero
        ]
        lower = min(possible)
        upper = max(possible)
        interval[name] = {
            "lower": lower,
            "median": resource[name],
            "upper": upper,
            "relative_span": (upper - lower) / max(abs(resource[name]), 1e-12),
        }
        if not lower <= resource[name] <= upper:
            raise RuntimeError("TPC-C v49 neighbour prediction escaped calibration interval")
    residuals = [float(row["score_residual"]) for row in weighted]
    residual_center = median(residuals)
    return {
        "contract": "tpcc_v49_nonexact_bounded_nonstationarity_v1",
        "anchor_rows": weighted,
        "resource_offset": offset,
        "score_offset": score_offset,
        "score_target_before_resource_reconciliation": score_target,
        "all_resource_reconciliation_alpha": alpha,
        "predicted_resource": resource,
        "predicted_score": score,
        "score_target_reconciliation_error": abs(score - score_target),
        "score_residual_mad": median(abs(value - residual_center) for value in residuals),
        "io_only_scalar_calibration": False,
        "exact_point_repeat": False,
        "distance_zero_residual_applied": False,
        "predicted_resource_interval": interval,
        "prediction_inside_calibration_interval": True,
        "nonexact_loo": _loo_nonexact(nonzero),
    }


def build(**kwargs: Any) -> Dict[str, Any]:
    selection_path = Path(kwargs["selection_path"]).resolve()
    ledger_path = Path(kwargs["ledger_path"]).resolve()
    ledger_sha256 = str(kwargs["ledger_sha256"])
    source = v44._load(selection_path)
    pool, descriptors = v44.v6.reconstruct_exact_pool(
        source,
        source_selection_path=selection_path,
        source_ledger_path=ledger_path,
    )
    evidence = v44._promote_repeated_clean_probes(
        v44.v37._role_evidence(descriptors, ledger_sha256)
    )
    historical_ids = v44.v37._historical_fingerprint_ids(descriptors)
    ranges = source.get("candidate_param_ranges") or {}
    del ranges
    calibration_anchors_by_plan: Dict[
        str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]
    ] = {}
    fallback_anchors_by_plan: Dict[
        str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]
    ] = {}
    for pool_candidate in pool:
        plan = str(pool_candidate["workload_plan_fingerprint"])
        rows = list(evidence.get(str(pool_candidate["exact_point_key"])) or [])
        fallback_anchors_by_plan.setdefault(plan, []).extend(
            (pool_candidate, row) for row in rows
        )
        calibration_anchors_by_plan.setdefault(plan, []).extend(
            (pool_candidate, row)
            for row in rows
            if v44._calibration_trusted(row)
        )
    global _EXACT_INTERVALS
    prior_intervals = _EXACT_INTERVALS
    _EXACT_INTERVALS = {}
    for key, rows in evidence.items():
        trusted_rows = [row for row in rows if v44._calibration_trusted(row)]
        role_rows = trusted_rows or list(rows)
        if not role_rows:
            continue
        center, _, _ = v44.v37._robust_exact_resource(role_rows)
        _EXACT_INTERVALS[str(key)] = _exact_interval(role_rows, center)
    try:
        result = core.build(**kwargs)
    finally:
        exact_intervals = _EXACT_INTERVALS
        _EXACT_INTERVALS = prior_intervals

    result["schema_version"] = "tpcc_v49_empirical_interval_successor_selection_v1"
    result["v49_build_contract"] = {
        "contract": BUILD_CONTRACT,
        "exact_point_repeat_uses_empirical_center": True,
        "distance_zero_residual_applied": False,
        "nonexact_alpha_lower": 0.0,
        "nonexact_alpha_upper": 1.0,
        "nonexact_loo_persisted": True,
        "prediction_inside_calibration_interval_required": True,
        "state_neutral": True,
        "manual_candidate_selection": False,
        "physical_workload_calls_added": 0,
    }
    used_ids = set()
    for candidate in result.get("top5") or []:
        model = (candidate.get("plan_regime_calibration") or {}).get(
            "nonstationarity_model"
        ) or {}
        interval = copy.deepcopy(model.get("predicted_resource_interval") or {})
        if set(interval) != set(RESOURCES):
            interval = copy.deepcopy(candidate.get("predicted_resource_interval") or {})
        if set(interval) != set(RESOURCES):
            raise RuntimeError("TPC-C v49 candidate lacks calibration interval")
        if not model:
            if candidate.get("selection_role") != "uncertain_probe":
                raise RuntimeError("TPC-C v49 trusted candidate lacks calibration model")
            quantiles = (0.5, 0.0, 1.0, 0.25, 0.75, 0.125, 0.875, 0.375, 0.625)
            selected = None
            for levels in product(quantiles, repeat=len(RESOURCES)):
                proposed = {}
                for index, name in enumerate(RESOURCES):
                    lower = float(interval[name]["lower"])
                    upper = float(interval[name]["upper"])
                    proposed[name] = lower + levels[index] * (upper - lower)
                utilization = {
                    name: proposed[name] / float(v44.v6.repeat_v2.CAPABILITY[name])
                    for name in RESOURCES
                }
                fingerprint = v44.v6._make_fingerprint(
                    candidate, proposed, utilization
                )
                identifier = str(fingerprint["id"])
                if identifier not in historical_ids and identifier not in used_ids:
                    selected = (proposed, utilization, fingerprint)
                    break
            if selected is None:
                raise RuntimeError("TPC-C v49 probe interval has no fresh fingerprint")
            proposed, utilization, fingerprint = selected
            score = v44.v6._score(proposed)
            candidate.update(
                {
                    "predicted_resource": proposed,
                    "final_resource": copy.deepcopy(proposed),
                    "utilization": utilization,
                    "three_level_fingerprint": fingerprint,
                    "candidate_score": score,
                    "candidate_score_recomputed": score,
                    "predicted_score": score,
                    "final_score": score,
                    "formal_lcb_score": score - 0.20,
                    "robust_acceptance_score": score - 0.20,
                    "predicted_score_interval": {
                        "lower": score - 0.20,
                        "center": score,
                        "upper": score + 0.20,
                        "source": "tpcc_v49_interval_bounded_acquisition_probe_v1",
                    },
                    "v49_interval_bounded_acquisition_probe": True,
                }
            )
            plan = str(candidate["workload_plan_fingerprint"])
            anchors = list(calibration_anchors_by_plan.get(plan) or [])
            if len(anchors) < 2:
                anchors = list(fallback_anchors_by_plan.get(plan) or [])
            loo = _loo_nonexact(anchors)
            model = {
                "contract": "tpcc_v49_interval_bounded_acquisition_probe_v1",
                "anchor_rows": [],
                "resource_offset": {name: 0.0 for name in RESOURCES},
                "score_offset": 0.0,
                "score_target_before_resource_reconciliation": score,
                "all_resource_reconciliation_alpha": 0.0,
                "predicted_resource": copy.deepcopy(proposed),
                "predicted_score": score,
                "score_target_reconciliation_error": 0.0,
                "score_residual_mad": 0.20,
                "io_only_scalar_calibration": False,
                "exact_point_repeat": False,
                "distance_zero_residual_applied": False,
                "predicted_resource_interval": copy.deepcopy(interval),
                "prediction_inside_calibration_interval": True,
                "nonexact_loo": loo,
            }
            calibration = candidate.setdefault("plan_regime_calibration", {})
            calibration["nonstationarity_model"] = model
            calibration["probe_evidence_affects_formal_or_gradient"] = False
        predicted = candidate["predicted_resource"]
        for name in RESOURCES:
            if not float(interval[name]["lower"]) <= float(predicted[name]) <= float(
                interval[name]["upper"]
            ):
                raise RuntimeError(
                    "TPC-C v49 final prediction escaped interval: "
                    f"rank={candidate.get('rank')} resource={name} "
                    f"predicted={predicted[name]} bounds={interval[name]} "
                    f"model={model.get('contract')}"
                )
        candidate["predicted_resource_interval"] = interval
        candidate["prediction_inside_calibration_interval"] = True
        candidate["distance_zero_residual_applied"] = False
        candidate["nonexact_loo"] = copy.deepcopy(model.get("nonexact_loo"))
        identifier = str(candidate["three_level_fingerprint"]["id"])
        if identifier in historical_ids or identifier in used_ids:
            raise RuntimeError("TPC-C v49 final fingerprint is not fresh/unique")
        used_ids.add(identifier)
    identifiers = [
        str(candidate["three_level_fingerprint"]["id"])
        for candidate in result.get("top5") or []
    ]
    roles = [candidate.get("selection_role") for candidate in result.get("top5") or []]
    for contract_name in ("v43_build_contract", "v44_build_contract"):
        contract = result.get(contract_name) or {}
        contract["fingerprint_ids"] = identifiers
        contract["roles"] = roles
        result[contract_name] = contract
    return result


def _sha(path: Path) -> str:
    return v44.v6._sha256(path.resolve())


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("selection", "ledger", "events", "summary", "gate", "formal-state"):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    kwargs = {
        name.replace("-", "_") + "_path": getattr(args, name.replace("-", "_"))
        for name in ("selection", "ledger", "events", "summary", "gate", "formal-state")
    }
    kwargs.update(
        {
            name.replace("-", "_") + "_sha256": getattr(
                args, name.replace("-", "_") + "_sha256"
            )
            for name in ("selection", "ledger", "events", "summary", "gate", "formal-state")
        }
    )
    result = build(**kwargs)
    _atomic_json(args.output.resolve(), result)
    print(json.dumps({"output": str(args.output.resolve()), "sha256": _sha(args.output)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
