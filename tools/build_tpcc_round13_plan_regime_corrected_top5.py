#!/usr/bin/env python3
"""Build a TPC-C Top-5 using exact, currently validated plan regimes."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resource_utility import ResourceUtilityCalculator


ATTEMPT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726"
)
ROUND11 = ATTEMPT / "gradient_debug_round11_tpcc_calibrated_deadband_v1_20260728"
ROUND12 = (
    ATTEMPT / "gradient_debug_noncorner_regime_discovery_v2_20260729T1702"
)
HISTORY = ROUND11 / "optimizer_history_2026-07-29_022342.jsonl"
CONFIG = ROUND11 / "config.json"
PLAN_CAPTURE = ATTEMPT / "plan_captures/baseline_correction_round_1_20260729_022824.json"
ESTIMATOR_MODULE = ROUND11 / "code/ratio_scale_resource_estimator.py"
DISCOVERY = ROUND12 / "top5.json"
LEDGER = ROUND12 / "strict_top5_real_replay_round12_ledger_v1.json"
OUTPUT = Path("/tmp/tpcc_round13_plan_regime_corrected_top5_v2.json")

EXPECTED_DISCOVERY_SHA = (
    "2063f33c6c2608b2a81893baed4e87fb40de738fecb31aceee7cf41dfd423d27"
)
CAPABILITY = {"cpu": 3.8, "memory": 6.0511731087, "io": 54.0}
RESOURCES = ("cpu", "memory", "io")
NON_CORNER_MARGIN = 0.05
LOG_TRANSFORM_PARAMS = {
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "cpu_tuple_cost",
    "parallel_tuple_cost",
    "random_page_cost",
    "seq_page_cost",
}
SCORE = ResourceUtilityCalculator(
    score_mode="band_target",
    resource_weights={"cpu": 1.0, "memory": 1.0, "io": 3.0},
    resource_target_ranges={
        "cpu": {
            "target_min_pct": 85.0,
            "target_max_pct": 90.0,
            "hard_overload_pct": 95.0,
            "role": "target_bottleneck",
        },
        "memory": {
            "target_min_pct": 30.0,
            "target_max_pct": 50.0,
            "hard_overload_pct": 90.0,
            "role": "non_bottleneck",
        },
        "io": {
            "target_min_pct": 30.0,
            "target_max_pct": 50.0,
            "hard_overload_pct": 90.0,
            "role": "non_bottleneck",
        },
    },
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def load_ranking() -> Dict[str, Any]:
    matches = []
    with HISTORY.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            event = json.loads(line)
            if event.get("event") == "experience_table_all_subset_ranking":
                event["_source_line_number"] = line_number
                matches.append(event)
    if len(matches) != 1 or matches[0].get("unique_candidate_count") != 12:
        raise RuntimeError("source ranking is not the sealed 12-candidate event")
    return matches[0]


def load_estimator_class() -> Any:
    sys.path.insert(0, str(ROUND11 / "code"))
    spec = importlib.util.spec_from_file_location(
        "tpcc_round13_ratio_estimator", ESTIMATOR_MODULE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load ratio estimator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RatioScaleResourceEstimator


def query_set(source_candidate: Mapping[str, Any]) -> List[Dict[str, Any]]:
    capture = json.loads(PLAN_CAPTURE.read_text(encoding="utf-8"))
    rows = source_candidate["final_estimation_metadata"]["query_features"]
    replay_count = {str(row["queryid"]): float(row["replay_count"]) for row in rows}
    result = []
    for item in capture["items"]:
        queryid = str(item["queryid"])
        if queryid not in replay_count:
            raise RuntimeError(f"query absent from ranking metadata: {queryid}")
        result.append(
            {
                "queryid": queryid,
                "query": item["query"],
                "replay_count": replay_count[queryid],
            }
        )
    if len(result) != len(rows):
        raise RuntimeError("query coverage mismatch")
    return result


def strict_interior(params: Mapping[str, Any], ranges: Mapping[str, Any]) -> bool:
    return all(
        float(bounds["min_value"]) < float(params[name]) < float(bounds["max_value"])
        for name, bounds in ranges.items()
    )


def transformed_fraction(
    name: str,
    value: float,
    bounds: Mapping[str, Any],
) -> float:
    lower = float(bounds["min_value"])
    upper = float(bounds["max_value"])
    if not lower < upper:
        raise RuntimeError(f"invalid parameter range: {name}")
    if name in LOG_TRANSFORM_PARAMS:
        if lower <= 0.0 or value <= 0.0:
            raise RuntimeError(f"log-transform parameter is not positive: {name}")
        return (math.log(value) - math.log(lower)) / (
            math.log(upper) - math.log(lower)
        )
    return (value - lower) / (upper - lower)


def is_non_corner(
    params: Mapping[str, Any],
    ranges: Mapping[str, Any],
    margin: float = NON_CORNER_MARGIN,
) -> bool:
    if not 0.0 < margin < 0.5:
        raise RuntimeError("non-corner margin must be between zero and one half")
    return all(
        margin
        <= transformed_fraction(name, float(params[name]), bounds)
        <= 1.0 - margin
        for name, bounds in ranges.items()
    )


def clipped_non_corner(
    params: Mapping[str, Any],
    ranges: Mapping[str, Any],
    margin: float = NON_CORNER_MARGIN,
) -> Dict[str, float]:
    result = {}
    safe_margin = margin + 1e-9
    for name, bounds in ranges.items():
        lower = float(bounds["min_value"])
        upper = float(bounds["max_value"])
        bounded_value = min(upper, max(lower, float(params[name])))
        if name in LOG_TRANSFORM_PARAMS:
            log_lower = math.log(lower)
            log_upper = math.log(upper)
            transformed = transformed_fraction(name, bounded_value, bounds)
            clipped = min(
                1.0 - safe_margin, max(safe_margin, transformed)
            )
            result[name] = math.exp(log_lower + clipped * (log_upper - log_lower))
        else:
            transformed = transformed_fraction(name, bounded_value, bounds)
            clipped = min(
                1.0 - safe_margin, max(safe_margin, transformed)
            )
            result[name] = lower + clipped * (upper - lower)
    if not strict_interior(result, ranges) or not is_non_corner(
        result, ranges, margin
    ):
        raise RuntimeError("failed to clip to non-corner interior")
    return result


def quantize(values: Mapping[str, Any], quantum: float = 0.005) -> Dict[str, int]:
    return {name: int(round(float(values[name]) / quantum)) for name in RESOURCES}


def fingerprint(
    plan: str,
    feature_ratio: Mapping[str, Any],
    resource: Mapping[str, Any],
) -> Dict[str, Any]:
    utilization = {
        name: float(resource[name]) / CAPABILITY[name] for name in RESOURCES
    }
    payload = {
        "plan": plan,
        "feature_ratio_q005": quantize(feature_ratio),
        "resource_utilization_q005": quantize(utilization),
    }
    return {
        "contract": "plan_feature_resource_three_level_v1",
        "id": "three-level-sha256:" + canonical_sha256(payload),
        "valid": True,
        "validation_errors": [],
        "plan_level": plan,
        "feature_ratio_level": quantize(feature_ratio),
        "predicted_resource_level": quantize(utilization),
        "parameter_fallback_used": False,
    }


def score(resource: Mapping[str, Any]) -> float:
    return float(SCORE.calculate_utility_score(dict(resource), CAPABILITY))


def delta_param(
    initial: Mapping[str, Any], params: Mapping[str, Any]
) -> Dict[str, Dict[str, Any]]:
    result = {}
    for name, initial_value in initial.items():
        before = float(initial_value)
        after = float(params[name])
        result[name] = {
            "before": before,
            "proposed_before_clip": after,
            "after_clip": after,
            "absolute": after - before,
            "relative": (after - before) / before if before else None,
            "clipped": False,
        }
    return result


def validate_ledger(
    discovery: Mapping[str, Any], ledger: Mapping[str, Any]
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, Any]]]:
    if (
        ledger.get("contract")
        != "tpcc_round12_current_plan_discovery_strict_serial_replay_v1"
        or ledger.get("real_workload_invocation_count") != 5
        or len(ledger.get("successful", [])) != 5
        or ledger.get("technical_failures") != []
    ):
        raise RuntimeError("round12 discovery ledger is not clean 5/5")
    selected = {int(item["rank"]): item for item in discovery["top5"]}
    successful = {int(item["rank"]): item for item in ledger["successful"]}
    if set(selected) != set(successful) or set(successful) != set(range(1, 6)):
        raise RuntimeError("round12 rank coverage mismatch")
    factors: Dict[str, Dict[str, float]] = {}
    evidence: Dict[str, Dict[str, Any]] = {}
    for rank in range(1, 6):
        candidate = selected[rank]
        result = successful[rank]
        if (
            result.get("diagnostic_only") is not True
            or result.get("eligible_for_incumbent_or_accepted") is not False
            or result["measurement_protocol"].get("correctness_passed") is not True
            or result["measurement_protocol"].get("stable") is not True
            or result["measurement_protocol"].get("technical_failure") is not False
            or result["three_level_fingerprint"]["id"]
            != candidate["three_level_fingerprint"]["id"]
            or result["workload_plan_fingerprint"]
            != candidate["workload_plan_fingerprint"]
        ):
            raise RuntimeError(f"round12 rank {rank} validation mismatch")
        summary_path = Path(result["measurement_protocol"]["validated_summary_path"])
        if sha256(summary_path) != result["measurement_protocol"]["validated_summary_sha256"]:
            raise RuntimeError(f"round12 rank {rank} summary SHA mismatch")
        plan = result["workload_plan_fingerprint"]
        if plan in factors:
            raise RuntimeError("discovery Top-5 unexpectedly repeated a plan regime")
        factor = {
            name: float(result["actual_resource"][name])
            / float(candidate["predicted_resource"][name])
            for name in RESOURCES
        }
        if any(not (0.1 <= value <= 10.0) for value in factor.values()):
            raise RuntimeError(f"plan-regime calibration magnitude failure: {plan}")
        factors[plan] = factor
        evidence[plan] = {
            "discovery_rank": rank,
            "three_level_fingerprint": result["three_level_fingerprint"]["id"],
            "summary_path": str(summary_path),
            "summary_sha256": sha256(summary_path),
            "predicted_resource": candidate["predicted_resource"],
            "actual_resource": result["actual_resource"],
            "resource_scalar": factor,
            "actual_score": result["actual_score"],
        }
    return factors, evidence


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    if sha256(DISCOVERY) != EXPECTED_DISCOVERY_SHA:
        raise RuntimeError("round12 discovery selection SHA mismatch")
    discovery = json.loads(DISCOVERY.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    factors, evidence = validate_ledger(discovery, ledger)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    ranking = load_ranking()
    raw_candidates = {
        int(item["rank"]): item for item in ranking["candidate_rankings"]
    }
    source_anchor = raw_candidates[1]
    baseline_params = {
        name: float(value) for name, value in source_anchor["initial_params"].items()
    }
    baseline_real = source_anchor["initial_resource"]
    queries = query_set(source_anchor)
    Estimator = load_estimator_class()
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

    used_ids = {
        item["three_level_fingerprint"]["id"] for item in discovery["top5"]
    }
    proposals: List[Tuple[Dict[str, float], Dict[str, Any], str]] = []
    for raw_rank, source in raw_candidates.items():
        proposals.append(
            (
                clipped_non_corner(
                    source["optimized_params"], config["param_ranges"]
                ),
                source,
                f"source_raw_rank_{raw_rank}",
            )
        )
    for seed in discovery["top5"]:
        seed_params = seed["optimized_params"]
        for name, bounds in config["param_ranges"].items():
            span = float(bounds["max_value"]) - float(bounds["min_value"])
            for fraction in (-0.05, -0.02, -0.01, 0.01, 0.02, 0.05):
                params = {key: float(value) for key, value in seed_params.items()}
                params[name] += span * fraction
                proposals.append(
                    (
                        clipped_non_corner(params, config["param_ranges"]),
                        seed,
                        f"discovery_rank_{seed['rank']}_{name}_{fraction:+.3f}",
                    )
                )

    candidates: List[Dict[str, Any]] = []
    seen_params = set()
    seen_ids = set(used_ids)
    for params, source, provenance in proposals:
        param_key = canonical_sha256(params)
        if param_key in seen_params:
            continue
        seen_params.add(param_key)
        raw_resource, _stats, metadata = estimator.estimate_with_metadata(queries, params)
        plan = str(metadata.get("workload_plan_fingerprint") or "")
        if not plan.startswith("ratio-plan-sha256:"):
            raise RuntimeError(f"proposal lacks plan fingerprint: {provenance}")
        feature_ratio = {
            name: float(metadata["feature_ratios"][name]) for name in RESOURCES
        }
        trusted = plan in factors
        predicted_resource = {
            name: float(raw_resource[name])
            * (factors[plan][name] if trusted else 1.0)
            for name in RESOURCES
        }
        fp = fingerprint(plan, feature_ratio, predicted_resource)
        if fp["id"] in seen_ids:
            continue
        seen_ids.add(fp["id"])
        candidate = copy.deepcopy(source)
        candidate.update(
            {
                "source_provenance": provenance,
                "selection_role": "trusted" if trusted else "uncertain_probe",
                "trusted": trusted,
                "uncertain_probe": not trusted,
                "exact_plan_regime_validated": trusted,
                "unknown_regime_policy": (
                    None if trusted else "probe_only_no_state_updates"
                ),
                "optimized_params": params,
                "params": params,
                "feature_ratio": feature_ratio,
                "uncalibrated_predicted_resource": {
                    name: float(raw_resource[name]) for name in RESOURCES
                },
                "predicted_resource": predicted_resource,
                "final_resource": predicted_resource,
                "utilization": {
                    name: predicted_resource[name] / CAPABILITY[name]
                    for name in RESOURCES
                },
                "candidate_score": score(predicted_resource),
                "predicted_score": score(predicted_resource),
                "final_score": score(predicted_resource),
                "delta_param": delta_param(baseline_params, params),
                "workload_plan_fingerprint": plan,
                "three_level_fingerprint": fp,
                "boundary_plateau": False,
                "boundary_corner": not is_non_corner(
                    params, config["param_ranges"]
                ),
                "probe_kind": "non_corner_plan_regime_candidate",
                "incumbent_update_eligible": False,
                "diagnostic_only": True,
                "qps_used_for_inner_scoring": False,
                "plan_regime_calibration": (
                    {
                        "mode": "exact_validated_plan_regime_scalar",
                        "resource_scalar": factors[plan],
                        "evidence": evidence[plan],
                    }
                    if trusted
                    else {
                        "mode": "unknown_plan_regime_probe_only",
                        "resource_scalar": None,
                    }
                ),
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
                raise RuntimeError(f"candidate missing required field: {required}")
        if candidate["boundary_corner"]:
            continue
        candidates.append(candidate)

    trusted_pool = sorted(
        (item for item in candidates if item["trusted"]),
        key=lambda item: (-float(item["candidate_score"]), item["source_provenance"]),
    )
    probe_pool = sorted(
        (item for item in candidates if not item["trusted"]),
        key=lambda item: (-float(item["candidate_score"]), item["source_provenance"]),
    )
    trusted = []
    trusted_plans = set()
    for candidate in trusted_pool:
        plan = candidate["workload_plan_fingerprint"]
        if plan in trusted_plans and len(trusted_plans) < 3:
            continue
        trusted.append(candidate)
        trusted_plans.add(plan)
        if len(trusted) == 3:
            break
    if len(trusted) < 3:
        for candidate in trusted_pool:
            if candidate not in trusted:
                trusted.append(candidate)
            if len(trusted) == 3:
                break
    probes = probe_pool[:2]
    if len(trusted) != 3 or len(probes) != 2:
        raise RuntimeError(
            f"insufficient corrected pool: trusted={len(trusted)}, probes={len(probes)}"
        )
    top5 = trusted + probes
    for rank, candidate in enumerate(top5, 1):
        candidate["rank"] = rank
        if (
            not strict_interior(candidate["optimized_params"], config["param_ranges"])
            or not is_non_corner(
                candidate["optimized_params"], config["param_ranges"]
            )
            or candidate["boundary_corner"]
        ):
            raise RuntimeError(f"rank {rank} is not a non-corner interior point")
    if [item["selection_role"] for item in top5] != [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]:
        raise RuntimeError("Top-5 role contract failure")
    ids = [item["three_level_fingerprint"]["id"] for item in top5]
    if len(set(ids)) != 5 or any(item in used_ids for item in ids):
        raise RuntimeError("Top-5 novelty or uniqueness failure")

    output = {
        "schema_version": 2,
        "selection_contract": "tpcc_round13_exact_plan_regime_corrected_top5_v1",
        "diagnostic_only": True,
        "state_mutation_contract": {
            "accepted_update": False,
            "incumbent_update": False,
            "range_update": False,
            "gradient_update": False,
        },
        "qps_role": "outer_audit_only_not_inner_score",
        "maximum_real_workload_invocations": 5,
        "source_history": str(HISTORY),
        "source_history_sha256": sha256(HISTORY),
        "source_ranking_line_number": ranking["_source_line_number"],
        "source_discovery": str(DISCOVERY),
        "source_discovery_sha256": EXPECTED_DISCOVERY_SHA,
        "source_discovery_ledger": str(LEDGER),
        "source_discovery_ledger_sha256": sha256(LEDGER),
        "source_config": str(CONFIG),
        "source_config_sha256": sha256(CONFIG),
        "candidate_pool_size": len(candidates),
        "trusted_plan_regimes": sorted(factors),
        "plan_regime_calibration_evidence": evidence,
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
                "sha256": sha256(OUTPUT),
                "candidate_pool_size": len(candidates),
                "trusted_pool_size": len(trusted_pool),
                "probe_pool_size": len(probe_pool),
                "roles": [item["selection_role"] for item in top5],
                "plans": [item["workload_plan_fingerprint"] for item in top5],
                "fingerprints": ids,
                "candidate_scores": [item["candidate_score"] for item in top5],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
