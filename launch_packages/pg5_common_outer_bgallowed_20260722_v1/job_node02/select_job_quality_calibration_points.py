#!/usr/bin/env python3
"""Select formal JOB quality-calibration points from the frozen 127 subsets."""

from __future__ import annotations

import copy
from concurrent.futures import Future
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from experience_table_subset_optimizer import optimize_all_experience_table_subsets
from gradient_descent_optimizer import ParameterRange
from parameter_optimizer import load_explicit_experience_table_contract
from plan_resource_estimator import PlanResourceEstimator


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
OUTPUT_ROOT = LANE_ROOT / "history/job/outer/attempt_1/job_quality_calibration"
CONFIG_PATH = (
    LANE_ROOT
    / "history/job/outer/attempt_1/generated_config/"
    "formal_job_outer_attempt1/memory/job.json"
)
BASELINE_RESULT_PATH = Path(
    os.environ.get(
        "JOB_QUALITY_BASELINE_RESULT",
        str(
            LANE_ROOT
            / "history/job/outer/attempt_1/formal_points/point_0004/point_result.json"
        ),
    )
)
ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
EXPECTED_TABLE_ARTIFACT_SHA256 = (
    "f5bfe293923e3c1122ca7363f3dc9b1b37d5f9c430540976535222188d8d7261"
)
EXPECTED_TABLE_SHA256 = (
    "a2fca76dd0ad04758ceebebb29a675ec43f1e52c5e820efca4b25a5fbaeaf07b"
)
QUERY_ORDER = ("1a", "5a", "9a", "12a", "16a", "20a", "26a", "33a")


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def exact_signature(params: Mapping[str, Any]) -> Tuple[Tuple[str, str], ...]:
    return tuple(
        (name, float(params[name]).hex())
        for name in sorted(ACTIVE_PARAMS)
    )


class SharedEstimateCache:
    """Deduplicate identical endpoint estimates across parallel subset runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._futures: Dict[Tuple[Tuple[str, str], ...], Future[Any]] = {}
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, params: Mapping[str, Any], compute: Any) -> Any:
        signature = exact_signature(params)
        owner = False
        with self._lock:
            future = self._futures.get(signature)
            if future is None:
                future = Future()
                self._futures[signature] = future
                self.misses += 1
                owner = True
            else:
                self.hits += 1
        if owner:
            try:
                future.set_result(compute())
            except BaseException as exc:
                future.set_exception(exc)
        return copy.deepcopy(future.result())

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "hits": self.hits,
                "misses": self.misses,
                "entries": len(self._futures),
            }


def formula_only_estimator_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(((cfg.get("metrics") or {}).get("plan_estimator") or {}))
    result.pop("model_retraining", None)
    result.setdefault("shared", {})["explain_workers"] = 1
    result.setdefault("logging", {})["log_topn"] = 8
    for resource in ("cpu", "io", "memory"):
        learned = result.setdefault(resource, {}).setdefault("learned_model", {})
        learned["enabled"] = False
        learned["required"] = False
    return result


def normalize_estimate(
    resource: Mapping[str, Any],
    stats: Mapping[str, Any],
) -> Dict[str, float]:
    def stat_avg(name: str, fallback: float = 0.0) -> float:
        item = stats.get(name)
        if isinstance(item, dict) and item.get("avg") is not None:
            return float(item["avg"])
        return fallback

    io_read = stat_avg("io_read")
    io_write = stat_avg("io_write")
    io_total = stat_avg("io", float(resource.get("io", 0.0) or 0.0))
    if io_read + io_write > 0.0 and io_total < 0.5 * (io_read + io_write):
        io_total = io_read + io_write
    return {
        "cpu": float(resource.get("cpu", 0.0) or 0.0),
        "memory": float(resource.get("memory", 0.0) or 0.0),
        "io": io_total,
    }


def transformed_coordinate(
    value: float,
    range_cfg: Mapping[str, Any],
) -> float:
    lower = float(range_cfg["min_value"])
    upper = float(range_cfg["max_value"])
    if str(range_cfg.get("transform") or "linear") == "log":
        value = math.log(max(value, 1e-300))
        lower = math.log(lower)
        upper = math.log(upper)
    return (value - lower) / (upper - lower)


def transformed_vector(
    params: Mapping[str, Any],
    ranges: Mapping[str, Mapping[str, Any]],
) -> List[float]:
    return [
        transformed_coordinate(float(params[name]), ranges[name])
        for name in ACTIVE_PARAMS
    ]


def distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def structural_plan_document(plan_doc: Any) -> Any:
    root = plan_doc.get("Plan") if isinstance(plan_doc, dict) else plan_doc

    def walk(node: Any) -> Any:
        if not isinstance(node, dict):
            return None
        result = {
            key: node[key]
            for key in (
                "Node Type",
                "Parent Relationship",
                "Parallel Aware",
                "Async Capable",
                "Join Type",
                "Strategy",
                "Partial Mode",
                "Operation",
                "Relation Name",
                "Index Name",
                "Scan Direction",
                "Workers Planned",
            )
            if key in node
        }
        children = [walk(child) for child in (node.get("Plans") or [])]
        result["Plans"] = [child for child in children if child is not None]
        return result

    return walk(root)


def operator_counts(plan_doc: Any) -> Dict[str, float]:
    root = plan_doc.get("Plan") if isinstance(plan_doc, dict) else plan_doc
    result: Dict[str, float] = {}

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        name = str(node.get("Node Type") or "Unknown")
        result[f"node::{name}"] = result.get(f"node::{name}", 0.0) + 1.0
        result["plan_rows"] = result.get("plan_rows", 0.0) + float(
            node.get("Plan Rows") or 0.0
        )
        result["plan_width"] = result.get("plan_width", 0.0) + float(
            node.get("Plan Width") or 0.0
        )
        result["plan_total_cost"] = result.get("plan_total_cost", 0.0) + float(
            node.get("Total Cost") or 0.0
        )
        result["workers_planned"] = result.get("workers_planned", 0.0) + float(
            node.get("Workers Planned") or 0.0
        )
        for child in node.get("Plans") or []:
            walk(child)

    walk(root)
    return result


def capture_plan_fingerprint(
    estimator: PlanResourceEstimator,
    queries: Sequence[Dict[str, Any]],
    params: Mapping[str, Any],
) -> Dict[str, Any]:
    explained = estimator._collect_query_plans(
        queries,
        explain_workers=8,
        params={name: float(params[name]) for name in ACTIVE_PARAMS},
    )
    failures = [item for item in explained if item.get("error")]
    if failures:
        raise RuntimeError(f"candidate EXPLAIN failures: {failures[:2]}")
    structural: List[Dict[str, Any]] = []
    aggregate: Dict[str, float] = {}
    plan_sha256_by_query: Dict[str, str] = {}
    for item in explained:
        query_id = str(item.get("queryid") or "")
        plan_doc = item.get("plan_doc")
        canonical = structural_plan_document(plan_doc)
        structural.append({"query_id": query_id, "plan": canonical})
        encoded_plan = json.dumps(
            plan_doc,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        plan_sha256_by_query[query_id] = hashlib.sha256(encoded_plan).hexdigest()
        weight = float(item.get("replay_count") or 1.0)
        for name, value in operator_counts(plan_doc).items():
            aggregate[name] = aggregate.get(name, 0.0) + weight * value
    encoded_structural = json.dumps(
        structural,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    feature_encoded = json.dumps(
        aggregate,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "structural_fingerprint": hashlib.sha256(encoded_structural).hexdigest(),
        "aggregate_plan_feature_sha256": hashlib.sha256(feature_encoded).hexdigest(),
        "aggregate_plan_features": aggregate,
        "plan_sha256_by_query": plan_sha256_by_query,
    }


def changed_params(
    params: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> List[str]:
    return [
        name
        for name in ACTIVE_PARAMS
        if float(params[name]).hex() != float(baseline[name]).hex()
    ]


def select_maximin_four(
    candidates: Sequence[Dict[str, Any]],
    baseline_vector: Sequence[float],
) -> List[Dict[str, Any]]:
    best: Tuple[Any, ...] | None = None
    best_combo: Tuple[Dict[str, Any], ...] | None = None
    for combo in itertools.combinations(candidates, 4):
        fingerprints = {item["structural_fingerprint"] for item in combo}
        if len(fingerprints) != 4:
            continue
        coverage = set().union(*(set(item["changed_params"]) for item in combo))
        if coverage != set(ACTIVE_PARAMS):
            continue
        vectors = [baseline_vector, *(item["transformed_vector"] for item in combo)]
        pairwise = [
            distance(vectors[left], vectors[right])
            for left in range(len(vectors))
            for right in range(left + 1, len(vectors))
        ]
        objective = (
            min(pairwise),
            sum(pairwise),
            sum(float(item.get("score_improvement") or 0.0) for item in combo),
            -sum(int(item.get("rank") or 0) for item in combo),
        )
        if best is None or objective > best:
            best = objective
            best_combo = combo
    if best_combo is None:
        raise RuntimeError(
            "no four-candidate combination has distinct structural fingerprints "
            "and covers all seven active parameters"
        )
    selected = [copy.deepcopy(item) for item in best_combo]
    for index, item in enumerate(selected, start=1):
        item["selection_order"] = index
        item["selection_objective"] = {
            "minimum_pairwise_transformed_distance": best[0],
            "sum_pairwise_transformed_distance": best[1],
        }
    return selected


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    cfg = read_json(CONFIG_PATH)
    active_params = list(((cfg.get("optimizer") or {}).get("gradient") or {}).get("active_params") or [])
    if tuple(active_params) != ACTIVE_PARAMS:
        raise RuntimeError(f"active parameter mismatch: {active_params}")
    sensitivity, binding = load_explicit_experience_table_contract(
        cfg,
        str(CONFIG_PATH),
        active_params,
    )
    if sensitivity is None:
        raise RuntimeError("explicit experience table was not loaded")
    if binding.get("artifact_sha256") != EXPECTED_TABLE_ARTIFACT_SHA256:
        raise RuntimeError(f"experience table artifact mismatch: {binding}")
    if binding.get("table_sha256") != EXPECTED_TABLE_SHA256:
        raise RuntimeError(f"experience table hash mismatch: {binding}")

    baseline_result = read_json(BASELINE_RESULT_PATH)
    initial_params = {
        name: float((cfg.get("initial_params") or {})[name])
        for name in ACTIVE_PARAMS
    }
    range_cfg = cfg.get("param_ranges") or {}
    param_ranges = {
        name: ParameterRange(
            min_value=float(range_cfg[name]["min_value"]),
            max_value=float(range_cfg[name]["max_value"]),
        )
        for name in ACTIVE_PARAMS
    }
    capability = {
        name: float(value)
        for name, value in (cfg.get("capability") or {}).items()
        if name in {"cpu", "memory", "io"}
    }
    requested_profile = (baseline_result.get("readback") or {}).get(
        "requested_resource_profile"
    ) or {}
    capability["memory"] = float(requested_profile["memory_bytes"]) / (1024.0 ** 3)
    means = (baseline_result.get("measurement") or {}).get(
        "resource_mean_utilization"
    ) or {}
    baseline_actual = {
        "cpu": float(means["cpu"]) * capability["cpu"],
        "memory": float(means["memory"]) * capability["memory"],
        "io": float(means["io"]) * capability["io"],
    }
    observed_counts = (baseline_result.get("measurement") or {}).get(
        "observed_query_counts"
    ) or {}
    queries = []
    for query_id in QUERY_ORDER:
        sql_path = LANE_ROOT / "assets/job_sql" / f"{query_id}.sql"
        queries.append(
            {
                "queryid": query_id,
                "query": sql_path.read_text(encoding="utf-8").strip().rstrip(";"),
                "replay_count": int(observed_counts[query_id]),
            }
        )

    estimator_cfg = formula_only_estimator_cfg(cfg)
    context = {
        "workload_family": "H",
        "benchmark_name": "job",
        "elapsed_seconds": 180.0,
        "feature_window_seconds": 5.0,
        "concurrency": {"avg": 2.0},
        "active_sql_count": 2.0,
        "cache_hit_rate": 0.976836989624045,
        "baseline_runtime_memory_bytes": baseline_actual["memory"] * (1024.0 ** 3),
        "runtime_resource_raw": {
            "cpu": baseline_actual["cpu"],
            "memory": baseline_actual["memory"] * (1024.0 ** 3),
            "io": baseline_actual["io"] * (1024.0 ** 2),
        },
    }
    os.environ.setdefault("PGPASSWORD", "CHANGE_ME")

    def new_estimator(*, explain_workers: int = 1) -> PlanResourceEstimator:
        local_cfg = copy.deepcopy(estimator_cfg)
        local_cfg.setdefault("shared", {})["explain_workers"] = explain_workers
        return PlanResourceEstimator(
            pg_host="127.0.0.1",
            pg_port="64309",
            pg_user="postgres",
            pg_dbname="imdb_job",
            plan_capture_modes=["generic"],
            estimator_cfg=local_cfg,
        )

    baseline_estimator = new_estimator()
    raw_resource, raw_stats, raw_metadata = baseline_estimator.estimate_resources_for_queries(
        queries,
        params=initial_params,
        workload_context=context,
    )
    raw_baseline = normalize_estimate(raw_resource, raw_stats)
    scales = {
        name: baseline_actual[name] / raw_baseline[name]
        for name in ("cpu", "memory", "io")
        if raw_baseline[name] > 0.0
    }
    if set(scales) != {"cpu", "memory", "io"}:
        raise RuntimeError(f"non-positive formula baseline: {raw_baseline}")

    cache = SharedEstimateCache()

    def estimate(params: Mapping[str, Any]) -> Tuple[Any, Any, Any]:
        estimator = new_estimator()
        resource, stats, metadata = estimator.estimate_resources_for_queries(
            queries,
            params={name: float(params[name]) for name in ACTIVE_PARAMS},
            workload_context=context,
        )
        normalized = normalize_estimate(resource, stats)
        scaled = {name: normalized[name] * scales[name] for name in scales}
        metadata["job_quality_calibration"] = {
            "source": "formal_127_subset_plan_formula_baseline_scaled",
            "formula_normalized": normalized,
            "baseline_scales": scales,
            "explicit_experience_table_binding": binding,
        }
        return scaled, stats, metadata

    def factory(_subset: Tuple[str, ...], _subset_id: str) -> Any:
        def cached_estimate(_sql_set: Any, params: Dict[str, float]) -> Any:
            return cache.get_or_compute(params, lambda: estimate(params))

        return cached_estimate

    gradient_cfg = (cfg.get("optimizer") or {}).get("gradient") or {}
    scoring_cfg = cfg.get("scoring") or {}
    summary = optimize_all_experience_table_subsets(
        resource_estimation_factory=factory,
        capability=capability,
        p_real=initial_params,
        sensitivity_table=sensitivity,
        param_ranges=param_ranges,
        sql_set={"benchmark": "job", "queries": list(QUERY_ORDER)},
        active_params=active_params,
        max_workers=int(gradient_cfg.get("subset_max_workers", 8)),
        round_index="job_quality_calibration_plan_only_v1",
        log_dir=str(OUTPUT_ROOT / "subset_logs"),
        optimizer_options={
            "eta": float(gradient_cfg.get("eta", 0.2)),
            "max_iter": int(gradient_cfg.get("max_iter", 20)),
            "patience": int(gradient_cfg.get("patience", 3)),
            "gamma": float(gradient_cfg.get("gamma", 0.5)),
            "reward_type": "sigmoid_linear",
            "penalty_type": "sigmoid_linear",
            "overload_threshold": float(scoring_cfg.get("overload_threshold", 0.8)),
            "score_mode": str(scoring_cfg.get("score_mode") or "band_target"),
            "resource_weights": copy.deepcopy(scoring_cfg.get("resource_weights") or {}),
            "resource_target_ranges": copy.deepcopy(
                scoring_cfg.get("resource_target_ranges") or {}
            ),
            "verbose": False,
        },
    )

    fingerprint_estimator = new_estimator(explain_workers=8)
    default_plan = capture_plan_fingerprint(
        fingerprint_estimator,
        queries,
        initial_params,
    )
    candidates: List[Dict[str, Any]] = []
    for item in summary.candidate_rankings:
        params = item.get("optimized_params") or {}
        changed = changed_params(params, initial_params)
        if not changed:
            continue
        plan = capture_plan_fingerprint(fingerprint_estimator, queries, params)
        candidate = {
            "rank": int(item["rank"]),
            "subset_id": item["subset_id"],
            "active_subset": list(item["active_subset"]),
            "changed_params": changed,
            "params": {name: float(params[name]) for name in ACTIVE_PARAMS},
            "transformed_vector": transformed_vector(params, range_cfg),
            "score_improvement": float(item.get("score_improvement") or 0.0),
            "final_score": float(item.get("final_score") or 0.0),
            **plan,
        }
        candidate["distance_from_default"] = distance(
            candidate["transformed_vector"],
            transformed_vector(initial_params, range_cfg),
        )
        candidates.append(candidate)
        write_json(
            OUTPUT_ROOT / "plan_only_candidates.partial.json",
            {
                "candidate_count": len(candidates),
                "candidates": candidates,
            },
        )

    eligible = [
        item
        for item in candidates
        if item["structural_fingerprint"] != default_plan["structural_fingerprint"]
    ]
    baseline_vector = transformed_vector(initial_params, range_cfg)
    selected = select_maximin_four(eligible, baseline_vector)
    selected_ids = {item["subset_id"] for item in selected}
    selected_fingerprints = {item["structural_fingerprint"] for item in selected}
    replacements = sorted(
        (
            copy.deepcopy(item)
            for item in eligible
            if item["subset_id"] not in selected_ids
            and item["structural_fingerprint"] not in selected_fingerprints
        ),
        key=lambda item: (-float(item["distance_from_default"]), int(item["rank"])),
    )
    artifact = {
        "schema_version": 1,
        "selection_id": "pg5_job_quality_calibration_plan_only_v1",
        "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "workload": "job",
        "decision_source": "formal explicit T all 127 non-empty subset endpoint framework",
        "answer_point_reference_used": False,
        "real_measurement_used_for_selection": False,
        "explicit_experience_table_binding": binding,
        "subset_summary": {
            "subset_count": summary.subset_count,
            "successful_subset_count": summary.successful_subset_count,
            "failed_subset_count": summary.failed_subset_count,
            "unique_candidate_count": summary.unique_candidate_count,
            "estimate_cache": cache.stats(),
            "log_dir": summary.log_dir,
        },
        "baseline": {
            "point_result_path": str(BASELINE_RESULT_PATH),
            "point_result_sha256": hashlib.sha256(
                BASELINE_RESULT_PATH.read_bytes()
            ).hexdigest(),
            "params": initial_params,
            "actual_resource": baseline_actual,
            "formula_resource_before_scale": raw_baseline,
            "formula_scale": scales,
            **default_plan,
        },
        "selection_constraints": {
            "selected_count": 4,
            "non_default": True,
            "distinct_structural_fingerprints": True,
            "cover_all_seven_active_parameters": True,
            "objective": "lexicographic maximin transformed distance including default, then sum distance",
        },
        "selected": selected,
        "replacement_queue": replacements,
        "all_candidates": candidates,
    }
    write_json(OUTPUT_ROOT / "calibration_selection.json", artifact)
    print(json.dumps({
        "status": "selected",
        "subset_count": summary.subset_count,
        "unique_candidate_count": summary.unique_candidate_count,
        "eligible_distinct_from_default_plan_count": len(eligible),
        "selected": [
            {
                "selection_order": item["selection_order"],
                "subset_id": item["subset_id"],
                "rank": item["rank"],
                "changed_params": item["changed_params"],
                "structural_fingerprint": item["structural_fingerprint"],
                "params": item["params"],
            }
            for item in selected
        ],
        "replacement_count": len(replacements),
        "output": str(OUTPUT_ROOT / "calibration_selection.json"),
    }, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
