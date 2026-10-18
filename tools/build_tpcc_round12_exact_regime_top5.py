#!/usr/bin/env python3
"""Build a fresh TPC-C current-plan discovery Top-5."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resource_utility import ResourceUtilityCalculator


ROUND11 = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "gradient_debug_round11_tpcc_calibrated_deadband_v1_20260728"
)
HISTORY = ROUND11 / "optimizer_history_2026-07-29_022342.jsonl"
LEDGER = ROUND11 / "strict_top5_real_replay_round11_ledger_v1.json"
OLD_TOP5 = ROUND11 / "tpcc_round11_calibrated_top5.json"
CONFIG = ROUND11 / "config.json"
PLAN_CAPTURE = (
    ROUND11.parent / "plan_captures/baseline_correction_round_1_20260729_022824.json"
)
ESTIMATOR_MODULE = ROUND11 / "code/ratio_scale_resource_estimator.py"
OUTPUT = Path("/tmp/tpcc_round12_current_plan_discovery_top5.json")

EXPECTED_SHA = {
    HISTORY: "97b8aab10a0baa01b24ee3e39180274db281f474274586b6f3a083cbf27629e8",
    LEDGER: "236db501e625f84fa07ea4dfc88619bd27b07850feadb7ff1a408b9c866015e5",
    OLD_TOP5: "08bd6ec14011d69c4517e8491b8528c7877329b7861416bd514d2378deddc307",
}
CAPABILITY = {"cpu": 3.8, "memory": 6.0511731087, "io": 54.0}
RESOURCES = ("cpu", "memory", "io")
VALIDATED_RAW_RANKS = (1, 3, 4)
DISCOVERY_RAW_RANKS = tuple(range(1, 13))
EXPECTED_ACTUAL = {
    1: 3.869559839017727,
    3: 3.644143331461419,
    4: 3.951409530383353,
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
    ranking = None
    with HISTORY.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            event = json.loads(line)
            if event.get("event") == "experience_table_all_subset_ranking":
                if ranking is not None:
                    raise RuntimeError("expected exactly one ranking event")
                ranking = event
                ranking["_source_line_number"] = line_number
    if ranking is None:
        raise RuntimeError("ranking event is missing")
    if ranking.get("unique_candidate_count") != 12:
        raise RuntimeError("unexpected source candidate count")
    return ranking


def load_estimator_class() -> Any:
    sys.path.insert(0, str(ROUND11 / "code"))
    spec = importlib.util.spec_from_file_location("tpcc_round12_ratio_estimator", ESTIMATOR_MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load ratio estimator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RatioScaleResourceEstimator


def query_set(source_candidate: Mapping[str, Any]) -> List[Dict[str, Any]]:
    capture = json.loads(PLAN_CAPTURE.read_text(encoding="utf-8"))
    items = capture.get("items")
    if not isinstance(items, list) or not items:
        raise RuntimeError("baseline plan capture has no items")
    feature_rows = (
        source_candidate.get("final_estimation_metadata", {})
        .get("query_features", [])
    )
    replay_count = {
        str(item["queryid"]): float(item["replay_count"])
        for item in feature_rows
    }
    result = []
    for item in items:
        queryid = str(item["queryid"])
        if queryid not in replay_count:
            raise RuntimeError(f"plan-capture query absent from ranking metadata: {queryid}")
        result.append(
            {
                "queryid": queryid,
                "query": item["query"],
                "replay_count": replay_count[queryid],
            }
        )
    if len(result) != len(feature_rows):
        raise RuntimeError(
            f"query coverage mismatch: capture={len(result)}, metadata={len(feature_rows)}"
        )
    return result


def strict_interior(params: Mapping[str, Any], ranges: Mapping[str, Any]) -> bool:
    return all(
        float(bounds["min_value"]) < float(params[name]) < float(bounds["max_value"])
        for name, bounds in ranges.items()
    )


def nudge_to_interior(
    source: Mapping[str, Any],
    ranges: Mapping[str, Any],
    fraction: float,
) -> Dict[str, float]:
    result = {name: float(value) for name, value in source.items()}
    for name, bounds in ranges.items():
        lower = float(bounds["min_value"])
        upper = float(bounds["max_value"])
        value = result[name]
        if value <= lower:
            value = lower + (upper - lower) * fraction
        elif value >= upper:
            value = upper - (upper - lower) * fraction
        result[name] = value
    if not strict_interior(result, ranges):
        raise RuntimeError("failed to construct strict interior parameters")
    return result


def quantize(values: Mapping[str, Any], quantum: float = 0.005) -> Dict[str, int]:
    return {name: int(round(float(values[name]) / quantum)) for name in RESOURCES}


def fingerprint(
    plan: str,
    feature_ratio: Mapping[str, Any],
    resource: Mapping[str, Any],
    baseline_real: Mapping[str, Any],
) -> Dict[str, Any]:
    feature_level = quantize(feature_ratio)
    utilization = {
        name: float(resource[name]) / CAPABILITY[name]
        for name in RESOURCES
    }
    resource_level = quantize(utilization)
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
        "resource_formula_audit": {
            name: {
                "baseline_real": float(baseline_real[name]),
                "feature_ratio": float(feature_ratio[name]),
                "expected_resource": (
                    float(baseline_real[name]) * float(feature_ratio[name])
                ),
                "observed_resource": float(resource[name]),
                "absolute_error": abs(
                    float(resource[name])
                    - float(baseline_real[name]) * float(feature_ratio[name])
                ),
            }
            for name in RESOURCES
        },
    }


def predicted_score(resource: Mapping[str, Any]) -> float:
    normalized = {name: float(resource[name]) for name in RESOURCES}
    return float(SCORE.calculate_utility_score(normalized, CAPABILITY))


def actual_by_raw_rank(ledger: Mapping[str, Any]) -> Dict[int, Dict[str, Any]]:
    result: Dict[int, Dict[str, Any]] = {}
    for item in ledger.get("successful", []):
        old_selection_rank = int(item["rank"])
        fp = item["three_level_fingerprint"]["id"]
        result[old_selection_rank] = item
        if not math.isclose(
            float(item["actual_score"]),
            EXPECTED_ACTUAL[{1: 1, 4: 3, 3: 4}[old_selection_rank]],
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise RuntimeError("validated actual-score evidence changed")
        if sha256(Path(item["measurement_protocol"]["validated_summary_path"])) != (
            item["measurement_protocol"]["validated_summary_sha256"]
        ):
            raise RuntimeError(f"validated summary SHA changed for {fp}")
    return {
        1: result[1],
        3: result[4],
        4: result[3],
    }


def delta_param(
    initial: Mapping[str, Any],
    params: Mapping[str, Any],
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


def required_fields(candidate: Mapping[str, Any]) -> None:
    for name in (
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
        if candidate.get(name) is None:
            raise RuntimeError(f"candidate missing required field: {name}")


def main() -> None:
    for path, expected in EXPECTED_SHA.items():
        observed = sha256(path)
        if observed != expected:
            raise RuntimeError(f"source SHA mismatch: {path}: {observed} != {expected}")

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    ranking = load_ranking()
    raw_candidates = {
        int(item["rank"]): item
        for item in ranking["candidate_rankings"]
    }
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    if ledger.get("real_workload_invocation_count") != 5:
        raise RuntimeError("round11 physical invocation count is not sealed at 5")
    _validated = actual_by_raw_rank(ledger)
    old_ids = {
        item["three_level_fingerprint"]["id"]
        for item in json.loads(OLD_TOP5.read_text(encoding="utf-8"))["top5"]
    }

    Estimator = load_estimator_class()
    source_anchor = raw_candidates[1]
    queries = query_set(source_anchor)
    estimator_cfg = copy.deepcopy(config["metrics"]["plan_estimator"])
    estimator = Estimator(
        pg_host="127.0.0.1",
        pg_port="59640",
        pg_user="postgres",
        pg_dbname="fullgrid_tpcc",
        estimator_cfg=estimator_cfg,
        plan_capture_modes=["generic"],
        verbose=False,
    )
    baseline_params = source_anchor["initial_params"]
    baseline_real = source_anchor["initial_resource"]
    estimator.calibrate(queries, baseline_params, baseline_real)

    discovery: List[Dict[str, Any]] = []
    explain_audit: List[Dict[str, Any]] = []
    for raw_rank in DISCOVERY_RAW_RANKS:
        source = raw_candidates[raw_rank]
        params = nudge_to_interior(
            source["optimized_params"],
            config["param_ranges"],
            1e-6,
        )
        resource, _stats, metadata = estimator.estimate_with_metadata(queries, params)
        observed_plan = str(metadata.get("workload_plan_fingerprint") or "")
        if not observed_plan.startswith("ratio-plan-sha256:"):
            raise RuntimeError(f"raw rank {raw_rank} lacks current plan fingerprint")
        explain_audit.append(
            {
                "source_raw_rank": raw_rank,
                "interior_nudge_fraction": 1e-6,
                "params": params,
                "source_plan": source["three_level_fingerprint"]["plan_level"],
                "current_plan": observed_plan,
                "source_plan_still_current": (
                    observed_plan == source["three_level_fingerprint"]["plan_level"]
                ),
            }
        )
        feature_ratio = {
            name: float(metadata["feature_ratios"][name])
            for name in RESOURCES
        }
        predicted_resource = {
            name: float(resource[name])
            for name in RESOURCES
        }
        for name in RESOURCES:
            expected = float(baseline_real[name]) * feature_ratio[name]
            if not math.isclose(
                predicted_resource[name], expected, rel_tol=1e-12, abs_tol=1e-12
            ):
                raise RuntimeError(
                    f"raw rank {raw_rank} resource formula mismatch for {name}: "
                    f"{predicted_resource[name]} != {expected}"
                )
        candidate = copy.deepcopy(source)
        candidate.update(
            {
                "source_raw_rank": raw_rank,
                "selection_role": "uncertain_probe",
                "trusted": False,
                "uncertain_probe": True,
                "exact_plan_regime_validated": False,
                "unknown_regime_policy": "probe_only_no_state_updates",
                "interior_nudge_fraction": 1e-6,
                "optimized_params": params,
                "params": params,
                "feature_ratio": feature_ratio,
                "predicted_resource": predicted_resource,
                "final_resource": predicted_resource,
                "utilization": {
                    name: predicted_resource[name] / CAPABILITY[name]
                    for name in RESOURCES
                },
                "candidate_score": predicted_score(predicted_resource),
                "predicted_score": predicted_score(predicted_resource),
                "final_score": predicted_score(predicted_resource),
                "delta_param": delta_param(source["initial_params"], params),
                "workload_plan_fingerprint": observed_plan,
                "three_level_fingerprint": fingerprint(
                    observed_plan,
                    feature_ratio,
                    predicted_resource,
                    baseline_real,
                ),
                "boundary_plateau": False,
                "boundary_corner": False,
                "incumbent_update_eligible": False,
                "diagnostic_only": True,
                "qps_used_for_inner_scoring": False,
            }
        )
        discovery.append(candidate)

    discovery.sort(
        key=lambda item: (-float(item["candidate_score"]), int(item["source_raw_rank"]))
    )
    top5 = []
    selected_ids = set()
    for candidate in discovery:
        fp_id = candidate["three_level_fingerprint"]["id"]
        if fp_id in old_ids or fp_id in selected_ids:
            continue
        selected_ids.add(fp_id)
        top5.append(candidate)
        if len(top5) == 5:
            break
    if len(top5) != 5:
        raise RuntimeError(
            f"current-plan discovery produced only {len(top5)} novel unique fingerprints"
        )
    for rank, candidate in enumerate(top5, 1):
        candidate["rank"] = rank
        required_fields(candidate)
        if not strict_interior(candidate["optimized_params"], config["param_ranges"]):
            raise RuntimeError(f"rank {rank} is not strict-interior")
        fp_id = candidate["three_level_fingerprint"]["id"]
        if fp_id in old_ids:
            raise RuntimeError(f"rank {rank} fingerprint is not novel")
    ids = [item["three_level_fingerprint"]["id"] for item in top5]
    if len(set(ids)) != 5:
        raise RuntimeError("Top-5 triple fingerprints are not unique")
    if [item["selection_role"] for item in top5] != ["uncertain_probe"] * 5:
        raise RuntimeError("discovery Top-5 role contract failed")

    output = {
        "schema_version": 2,
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
        "source_history": str(HISTORY),
        "source_history_sha256": EXPECTED_SHA[HISTORY],
        "source_ranking_line_number": ranking["_source_line_number"],
        "source_ledger": str(LEDGER),
        "source_ledger_sha256": EXPECTED_SHA[LEDGER],
        "source_config": str(CONFIG),
        "source_config_sha256": sha256(CONFIG),
        "source_plan_capture": str(PLAN_CAPTURE),
        "source_plan_capture_sha256": sha256(PLAN_CAPTURE),
        "candidate_pool_size": len(raw_candidates),
        "trusted_plan_regimes": [],
        "discovery_reason": (
            "all three previously validated plan fingerprints drifted after baseline "
            "restore; unknown current regimes must remain probe-only"
        ),
        "explain_only_audit": explain_audit,
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
                "roles": [item["selection_role"] for item in top5],
                "fingerprints": ids,
                "candidate_scores": [item["candidate_score"] for item in top5],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
