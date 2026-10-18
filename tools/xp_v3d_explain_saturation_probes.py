#!/usr/bin/env python3
"""Generate XP non-corner EXPLAIN-only probes along normalized raw gradients."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


RESOURCES = ("cpu", "io", "memory")
TARGET_RELATIVE_STEPS = (0.05, 0.10, 0.20, 0.40, 0.80)
CONTRACT = "xp_round10_explain_only_normalized_gradient_saturation_v1"


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def finite(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def first_gradient_events(log_root: Path) -> List[Tuple[str, Dict[str, Any]]]:
    events: List[Tuple[str, Dict[str, Any]]] = []
    for path in sorted(log_root.rglob("subset_*.jsonl")):
        first = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if value.get("event") == "score_gradient_debug":
                first = value
                break
        if first is None:
            raise ValueError(f"{path} lacks score_gradient_debug")
        events.append((path.stem, first))
    if len(events) != 63:
        raise ValueError(f"expected 63 subset gradients, got {len(events)}")
    return events


def strict_candidate_params(
    initial: Mapping[str, Any],
    ranges: Mapping[str, Any],
    gradient: Mapping[str, Any],
    target_relative_step: float,
) -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]], float]:
    parsed_gradient = {
        str(name): finite(value, f"g_j.{name}") for name, value in gradient.items()
    }
    peak = max((abs(value) for value in parsed_gradient.values()), default=0.0)
    if peak <= 0.0:
        raise ValueError("gradient direction has zero magnitude")
    eta = target_relative_step / peak
    candidate = {str(name): finite(value, f"initial.{name}") for name, value in initial.items()}
    audit: Dict[str, Dict[str, Any]] = {}
    for name, direction in parsed_gradient.items():
        before = candidate[name]
        raw = before * (1.0 + eta * direction)
        bounds = ranges[name]
        lower = finite(bounds.get("min_value", bounds.get("min")), f"range.{name}.min")
        upper = finite(bounds.get("max_value", bounds.get("max")), f"range.{name}.max")
        if lower > upper:
            raise ValueError(f"invalid range for {name}")
        if lower == upper:
            after = lower
        else:
            strict_lower = math.nextafter(lower, upper)
            strict_upper = math.nextafter(upper, lower)
            after = min(strict_upper, max(strict_lower, raw))
        candidate[name] = after
        relative = (after - before) / before
        if abs(relative) > 0.800000000001:
            raise ValueError(f"relative step exceeds 80% for {name}: {relative}")
        audit[name] = {
            "before": before,
            "proposed_before_clip": raw,
            "after_clip": after,
            "absolute": after - before,
            "relative": relative,
            "clipped": after != raw,
        }
    return candidate, audit, eta


def quantized_mapping(raw: Mapping[str, Any], quantum: float) -> Dict[str, int]:
    return {
        str(name): int(round(finite(value, str(name)) / quantum))
        for name, value in raw.items()
    }


def three_level_fingerprint(
    metadata: Mapping[str, Any],
    predicted: Mapping[str, Any],
    capability: Mapping[str, Any],
) -> Dict[str, Any]:
    plan = str(metadata.get("workload_plan_fingerprint") or "")
    if not plan.startswith("ratio-plan-sha256:"):
        raise ValueError("validated ratio plan fingerprint is missing")
    feature_ratio = metadata.get("feature_ratios")
    baseline_real = metadata.get("baseline_real")
    if not isinstance(feature_ratio, Mapping) or not isinstance(baseline_real, Mapping):
        raise ValueError("ratio-scale metadata is incomplete")
    feature_level = quantized_mapping(feature_ratio, 0.005)
    utilization = {
        resource: finite(predicted[resource], f"predicted.{resource}")
        / finite(capability[resource], f"capability.{resource}")
        for resource in RESOURCES
    }
    resource_level = quantized_mapping(utilization, 0.005)
    audit = {}
    for resource in RESOURCES:
        observed = finite(predicted[resource], f"predicted.{resource}")
        expected = finite(baseline_real[resource], f"baseline_real.{resource}") * finite(
            feature_ratio[resource], f"feature_ratio.{resource}"
        )
        absolute_error = abs(observed - expected)
        allowed_error = max(1e-12, 1e-12 * max(abs(observed), abs(expected)))
        if absolute_error > allowed_error:
            raise ValueError(f"predicted resource formula mismatch for {resource}")
        audit[resource] = {
            "baseline_real": finite(baseline_real[resource], resource),
            "feature_ratio": finite(feature_ratio[resource], resource),
            "expected_resource": expected,
            "observed_resource": observed,
            "absolute_error": absolute_error,
            "allowed_error": allowed_error,
        }
    payload = {
        "plan": plan,
        "feature_ratio_q005": feature_level,
        "resource_utilization_q005": resource_level,
    }
    return {
        "contract": "plan_feature_resource_three_level_v1",
        "id": "three-level-sha256:" + canonical_sha256(payload),
        "valid": True,
        "validation_errors": [],
        "plan_level": plan,
        "feature_ratio_level": feature_level,
        "predicted_resource_level": resource_level,
        "parameter_fallback_used": False,
        "resource_formula_audit": audit,
    }


def authoritative_ids(selection: Mapping[str, Any]) -> set[str]:
    candidates = selection.get("top5") or selection.get("candidate_rankings")
    if not isinstance(candidates, list):
        raise ValueError("authoritative selection lacks candidates")
    return {
        str(candidate["three_level_fingerprint"]["id"])
        for candidate in candidates
        if isinstance(candidate, Mapping)
        and isinstance(candidate.get("three_level_fingerprint"), Mapping)
        and candidate["three_level_fingerprint"].get("id")
    }


def calibrated_plan_ids(calibration: Mapping[str, Any]) -> set[str]:
    anchors = calibration.get("anchors")
    if not isinstance(anchors, list):
        raise ValueError("plan calibration lacks anchors")
    result = set()
    for anchor in anchors:
        if not isinstance(anchor, Mapping):
            continue
        value = anchor.get("workload_plan_fingerprint")
        if value:
            result.add(str(value))
    return result


def validated_signature_match(metadata: Mapping[str, Any]) -> bool:
    calibration = metadata.get("plan_signature_calibration")
    if not isinstance(calibration, Mapping) or calibration.get("enabled") is not True:
        return False
    matches = calibration.get("matches")
    if not isinstance(matches, Mapping):
        return False
    anchor_ranks = None
    for resource in RESOURCES:
        match = matches.get(resource)
        if not isinstance(match, Mapping):
            return False
        similarity = finite(match.get("similarity"), f"{resource}.similarity")
        minimum = finite(
            match.get("minimum_similarity"),
            f"{resource}.minimum_similarity",
        )
        if similarity < minimum or match.get("fallback_reason") is not None:
            return False
        ranks = tuple(int(value) for value in (match.get("anchor_ranks") or []))
        if not ranks:
            return False
        if anchor_ranks is None:
            anchor_ranks = ranks
        elif ranks != anchor_ranks:
            return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--gradient-log-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pg-port", type=int, default=59630)
    args = parser.parse_args()

    root = args.bundle_root.resolve()
    sys.path.insert(0, str(root))
    import resource_utility  # type: ignore
    import xp_round10_explain_only as explain  # type: ignore

    selection_path = root / "authoritative_round9/selection.json"
    config_path = root / "authoritative_round9/config.json"
    incumbent_path = root / "authoritative_round9/accepted_incumbent.json"
    query_path = root / "query_manifest.json"
    formal_path = root / "authoritative_round9/ratio_scale_resource_estimator.py"
    calibration_path = root / "plan_signature_calibration.json"

    sealed, config, incumbent, queries = explain._load_inputs(
        selection_path,
        config_path,
        incumbent_path,
        query_path,
    )
    incumbent_before = canonical_sha256(incumbent)
    initial = sealed["top5"][0]["initial_params"]
    initial_resource = sealed["top5"][0]["initial_resource"]
    capability = config["capability"]
    ranges = config["param_ranges"]
    calibrated_plans = calibrated_plan_ids(load_object(calibration_path))
    prior_ids = authoritative_ids(sealed)
    gradient_events = first_gradient_events(args.gradient_log_root)

    postgres = dict(config.get("postgres") or {})
    postgres.update(
        {
            "host": "127.0.0.1",
            "port": args.pg_port,
            "user": "postgres",
            "dbname": "hybench_sf10x",
        }
    )
    formal = explain._load_formal_ratio_module(formal_path)
    estimator_config = explain._formal_estimator_config(config, calibration_path)
    session = explain.ExplainOnlySession.connect(
        host="127.0.0.1",
        port=args.pg_port,
        user="postgres",
        dbname="hybench_sf10x",
        password=None,
    )
    try:
        factory = explain._build_factory(
            formal_module=formal,
            estimator_config=estimator_config,
            session=session,
            initial_params=initial,
            initial_resource=initial_resource,
            queries=queries,
            postgres=postgres,
        )
        estimator = factory(tuple(sorted(ranges)), "v3d_normalized_gradient_saturation")
        calculator = resource_utility.ResourceUtilityCalculator(
            score_mode="band_target",
            resource_weights={"cpu": 1.0, "io": 3.0, "memory": 1.0},
            resource_target_ranges={
                "cpu": {"min": 0.30, "max": 0.50},
                "io": {"min": 0.85, "max": 0.90},
                "memory": {"min": 0.30, "max": 0.50},
            },
        )
        with explain.no_workload_runtime_guard():
            baseline_predicted = estimator(queries, initial)
            baseline_metadata = copy.deepcopy(estimator.last_metadata)
            baseline_plan = str(
                baseline_metadata.get("workload_plan_fingerprint") or ""
            )
            candidates: List[Dict[str, Any]] = []
            probe_audit: List[Dict[str, Any]] = []
            for subset_id, debug in gradient_events:
                gradient = debug.get("g_j")
                if not isinstance(gradient, Mapping):
                    raise ValueError(f"{subset_id} lacks g_j")
                parsed_gradient = {
                    str(name): finite(value, f"g_j.{name}")
                    for name, value in gradient.items()
                }
                direction_tracks = (
                    ("forward_original_gradient", parsed_gradient),
                    (
                        "reverse_uncertain_acquisition",
                        {name: -value for name, value in parsed_gradient.items()},
                    ),
                )
                for direction_track, directional_gradient in direction_tracks:
                    for probe_index, target_step in enumerate(
                        TARGET_RELATIVE_STEPS, 1
                    ):
                        params, delta, eta = strict_candidate_params(
                            initial,
                            ranges,
                            directional_gradient,
                            target_step,
                        )
                        predicted = estimator(queries, params)
                        metadata = copy.deepcopy(estimator.last_metadata)
                        plan = str(metadata.get("workload_plan_fingerprint") or "")
                        changed = plan != baseline_plan
                        probe_audit.append(
                            {
                                "subset_id": subset_id,
                                "direction_track": direction_track,
                                "probe_index": probe_index,
                                "target_max_relative_step": target_step,
                                "eta": eta,
                                "candidate_params": params,
                                "plan_fingerprint": plan,
                                "plan_changed": changed,
                            }
                        )
                        if not changed:
                            continue
                        fingerprint = three_level_fingerprint(
                            metadata,
                            predicted,
                            capability,
                        )
                        utilization = {
                            resource: finite(predicted[resource], resource)
                            / finite(capability[resource], resource)
                            for resource in RESOURCES
                        }
                        trusted_signature = validated_signature_match(
                            metadata
                        )
                        selected = {
                            "subset_id": f"{subset_id}__{direction_track}",
                            "active_subset": sorted(
                                str(name) for name in directional_gradient
                            ),
                            "candidate_origin": (
                                "normalized_gradient_direction_track_"
                                "first_plan_change"
                            ),
                            "probe_kind": "gradient_saturation_plan_change_probe",
                            "direction_track": direction_track,
                            "original_g_j": copy.deepcopy(parsed_gradient),
                            "adaptive_plan_change_probe_index": probe_index,
                            "requested_relative_step_pct": target_step * 100.0,
                            "initial_params": copy.deepcopy(initial),
                            "candidate_params": params,
                            "optimized_params": copy.deepcopy(params),
                            "initial_resource": copy.deepcopy(initial_resource),
                            "predicted_resource": copy.deepcopy(predicted),
                            "final_resource": copy.deepcopy(predicted),
                            "feature_ratio": copy.deepcopy(
                                metadata.get("feature_ratios")
                            ),
                            "utilization": utilization,
                            "dScore/dU": copy.deepcopy(debug.get("dScore/dU")),
                            "T": copy.deepcopy(debug.get("T")),
                            "confidence": copy.deepcopy(debug.get("confidence")),
                            "g_j": copy.deepcopy(directional_gradient),
                            "eta": eta,
                            "eta_semantics": (
                                "directional_gradient_multiplier_normalized_"
                                "to_target_relative_step"
                            ),
                            "delta_param": delta,
                            "candidate_score": calculator.calculate_utility_score(
                                predicted,
                                capability,
                            ),
                            "final_score": calculator.calculate_utility_score(
                                predicted,
                                capability,
                            ),
                            "final_estimation_metadata": metadata,
                            "workload_plan_fingerprint": plan,
                            "plan_fingerprint_before": baseline_plan,
                            "plan_fingerprint_after": plan,
                            "plan_fingerprint_changed": True,
                            "three_level_fingerprint": fingerprint,
                            "candidate_plan_signature": fingerprint["id"],
                            "calibrated_plan_regime": trusted_signature,
                            "selection_role_eligibility": (
                                "trusted" if trusted_signature else "probe_only"
                            ),
                            "novel_against_authoritative_round9": (
                                fingerprint["id"] not in prior_ids
                            ),
                            "non_corner": True,
                            "used_for_gradient": False,
                            "used_for_inner_incumbent_update": False,
                            "incumbent_update_eligible": False,
                            "qps_used_for_inner_scoring": False,
                            "workload_executed": False,
                        }
                        candidates.append(selected)
                        break
    finally:
        session.close()

    incumbent_after = canonical_sha256(
        load_object(incumbent_path)
    )
    if incumbent_after != incumbent_before:
        raise RuntimeError("accepted incumbent changed during EXPLAIN-only generation")

    unique: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        identifier = candidate["three_level_fingerprint"]["id"]
        if not candidate["novel_against_authoritative_round9"]:
            continue
        current = unique.get(identifier)
        if current is None or candidate["candidate_score"] > current["candidate_score"]:
            unique[identifier] = candidate
    ranked = sorted(
        unique.values(),
        key=lambda candidate: (
            -candidate["candidate_score"],
            candidate["subset_id"],
        ),
    )
    trusted_count = sum(
        candidate["selection_role_eligibility"] == "trusted"
        for candidate in ranked
    )
    result = {
        "contract": CONTRACT,
        "status": (
            "ready"
            if len(ranked) >= 5 and trusted_count >= 3
            else "blocked_explain_only"
        ),
        "offline_only": True,
        "explain_only": True,
        "workload_executed": False,
        "qps_used_for_inner_scoring": False,
        "target_relative_steps": list(TARGET_RELATIVE_STEPS),
        "max_relative_step": 0.80,
        "direction_source": "original_dScore_dU_times_T_times_confidence",
        "first_legal_plan_change_only": True,
        "baseline_predicted_resource": baseline_predicted,
        "baseline_plan_fingerprint": baseline_plan,
        "gradient_log_root": str(args.gradient_log_root.resolve()),
        "gradient_log_digest": canonical_sha256(
            [
                {
                    "subset_id": subset_id,
                    "g_j": event.get("g_j"),
                    "T": event.get("T"),
                    "confidence": event.get("confidence"),
                    "dScore/dU": event.get("dScore/dU"),
                }
                for subset_id, event in gradient_events
            ]
        ),
        "authoritative_selection_sha256": sha256_file(selection_path),
        "accepted_incumbent_sha256_before": incumbent_before,
        "accepted_incumbent_sha256_after": incumbent_after,
        "accepted_incumbent_mutated": False,
        "explain_query_count": session.query_count,
        "raw_plan_change_candidate_count": len(candidates),
        "novel_non_corner_fingerprint_count": len(ranked),
        "trusted_eligible_count": trusted_count,
        "candidate_rankings": ranked,
        "probe_audit": probe_audit,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "novel_non_corner_fingerprint_count": len(ranked),
                "trusted_eligible_count": trusted_count,
                "explain_query_count": result["explain_query_count"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if result["status"] != "ready":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
