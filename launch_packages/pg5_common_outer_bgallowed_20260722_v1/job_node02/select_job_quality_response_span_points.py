#!/usr/bin/env python3
"""Select a second JOB calibration batch by predicted response span only."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from plan_resource_estimator import PlanResourceEstimator


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
OUTER_ROOT = LANE_ROOT / "history/job/outer/attempt_1"
CALIBRATION_ROOT = OUTER_ROOT / "job_quality_calibration"
FIRST_SELECTION_PATH = CALIBRATION_ROOT / "calibration_selection.json"
CONFIG_PATH = OUTER_ROOT / "generated_config/formal_job_outer_attempt1/memory/job.json"
OUTPUT_ROOT = CALIBRATION_ROOT / "response_span_batch2"
OUTPUT_PATH = OUTPUT_ROOT / "response_span_selection.json"
ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
QUERY_ORDER = ("1a", "5a", "9a", "12a", "16a", "20a", "26a", "33a")
RESOURCES = ("cpu", "io", "memory")
EXTREME_COUNT = 20


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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def formula_only_estimator_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(((cfg.get("metrics") or {}).get("plan_estimator") or {}))
    result.pop("model_retraining", None)
    result.setdefault("shared", {})["explain_workers"] = 1
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
        "io": io_total,
        "memory": float(resource.get("memory", 0.0) or 0.0),
    }


def distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def selection_objective(
    combo: Sequence[Dict[str, Any]],
    resource_min: Mapping[str, float],
    resource_max: Mapping[str, float],
) -> Tuple[float, float, float, float]:
    normalized_spans: List[float] = []
    for resource in RESOURCES:
        values = [float(item["predicted_formula_resource"][resource]) for item in combo]
        denominator = max(resource_max[resource] - resource_min[resource], 1e-18)
        normalized_spans.append((max(values) - min(values)) / denominator)
    vectors = [item["transformed_vector"] for item in combo]
    pairwise = [
        distance(vectors[left], vectors[right])
        for left in range(len(vectors))
        for right in range(left + 1, len(vectors))
    ]
    return (
        min(normalized_spans),
        sum(normalized_spans),
        min(pairwise),
        sum(float(item.get("distance_from_default") or 0.0) for item in combo),
    )


def main() -> int:
    os.environ.setdefault("PGPASSWORD", "CHANGE_ME")
    cfg = read_json(CONFIG_PATH)
    first = read_json(FIRST_SELECTION_PATH)
    first_ids = {str(item["subset_id"]) for item in first.get("selected") or []}
    baseline_result = read_json(OUTER_ROOT / "formal_points/point_0004/point_result.json")
    observed_counts = (baseline_result.get("measurement") or {}).get(
        "observed_query_counts"
    ) or {}
    queries = [
        {
            "queryid": query_id,
            "query": (LANE_ROOT / "assets/job_sql" / f"{query_id}.sql")
            .read_text(encoding="utf-8")
            .strip()
            .rstrip(";"),
            "replay_count": int(observed_counts[query_id]),
        }
        for query_id in QUERY_ORDER
    ]
    estimator = PlanResourceEstimator(
        pg_host="127.0.0.1",
        pg_port="64309",
        pg_user="postgres",
        pg_dbname="imdb_job",
        plan_capture_modes=["generic"],
        estimator_cfg=formula_only_estimator_cfg(cfg),
    )
    baseline_actual = (first.get("baseline") or {}).get("actual_resource") or {}
    context = {
        "workload_family": "H",
        "benchmark_name": "job",
        "elapsed_seconds": 180.0,
        "feature_window_seconds": 5.0,
        "concurrency": {"avg": 2.0},
        "active_sql_count": 2.0,
        "cache_hit_rate": 0.976836989624045,
        "baseline_runtime_memory_bytes": float(baseline_actual["memory"]) * 1024.0 ** 3,
    }
    candidates: List[Dict[str, Any]] = []
    for source in first.get("all_candidates") or []:
        if str(source.get("subset_id")) in first_ids:
            continue
        params = {name: float((source.get("params") or {})[name]) for name in ACTIVE_PARAMS}
        resource, stats, _ = estimator.estimate_resources_for_queries(
            queries,
            params=params,
            workload_context=context,
        )
        item = copy.deepcopy(source)
        item["predicted_formula_resource"] = normalize_estimate(resource, stats)
        candidates.append(item)
    if len(candidates) < 4:
        raise RuntimeError("fewer than four unused plan-only candidates")

    sorted_by_resource = {
        resource: sorted(
            candidates,
            key=lambda item: (
                float(item["predicted_formula_resource"][resource]),
                int(item["rank"]),
            ),
        )
        for resource in RESOURCES
    }
    resource_min = {
        resource: float(sorted_by_resource[resource][0]["predicted_formula_resource"][resource])
        for resource in RESOURCES
    }
    resource_max = {
        resource: float(sorted_by_resource[resource][-1]["predicted_formula_resource"][resource])
        for resource in RESOURCES
    }
    extreme_ids = set()
    low_ids: Dict[str, set[str]] = {}
    high_ids: Dict[str, set[str]] = {}
    for resource in RESOURCES:
        low = sorted_by_resource[resource][:EXTREME_COUNT]
        high = sorted_by_resource[resource][-EXTREME_COUNT:]
        low_ids[resource] = {str(item["subset_id"]) for item in low}
        high_ids[resource] = {str(item["subset_id"]) for item in high}
        extreme_ids.update(low_ids[resource])
        extreme_ids.update(high_ids[resource])
    frontier = [
        item for item in candidates if str(item["subset_id"]) in extreme_ids
    ]

    best: Tuple[float, float, float, float] | None = None
    best_combo: Tuple[Dict[str, Any], ...] | None = None
    best_coverage = -1
    feasible_count = 0
    for combo in itertools.combinations(frontier, 4):
        if len({item["structural_fingerprint"] for item in combo}) != 4:
            continue
        combo_ids = {str(item["subset_id"]) for item in combo}
        if any(
            not (combo_ids & low_ids[resource])
            or not (combo_ids & high_ids[resource])
            for resource in RESOURCES
        ):
            continue
        coverage = len(set().union(*(set(item["changed_params"]) for item in combo)))
        objective = selection_objective(combo, resource_min, resource_max)
        feasible_count += 1
        if coverage > best_coverage or (
            coverage == best_coverage and (best is None or objective > best)
        ):
            best_coverage = coverage
            best = objective
            best_combo = combo
    if best_combo is None or best is None:
        raise RuntimeError("no four-point response-extreme combination satisfies the constraints")

    selected = [copy.deepcopy(item) for item in best_combo]
    for order, item in enumerate(selected, start=1):
        item["selection_order"] = order
        item["response_roles"] = [
            f"{resource}_{end}"
            for resource in RESOURCES
            for end, ids in (("low", low_ids[resource]), ("high", high_ids[resource]))
            if str(item["subset_id"]) in ids
        ]
    selected_ids = {str(item["subset_id"]) for item in selected}
    selected_fingerprints = {item["structural_fingerprint"] for item in selected}
    replacements = sorted(
        (
            copy.deepcopy(item)
            for item in frontier
            if str(item["subset_id"]) not in selected_ids
            and item["structural_fingerprint"] not in selected_fingerprints
        ),
        key=lambda item: (
            -max(
                abs(
                    (
                        float(item["predicted_formula_resource"][resource])
                        - resource_min[resource]
                    )
                    / max(resource_max[resource] - resource_min[resource], 1e-18)
                    - 0.5
                )
                for resource in RESOURCES
            ),
            int(item["rank"]),
        ),
    )
    predicted_span = {}
    for resource in RESOURCES:
        values = [float(item["predicted_formula_resource"][resource]) for item in selected]
        predicted_span[resource] = {
            "selected_min": min(values),
            "selected_max": max(values),
            "selected_absolute_span": max(values) - min(values),
            "pool_min": resource_min[resource],
            "pool_max": resource_max[resource],
            "pool_span_fraction": (
                (max(values) - min(values))
                / max(resource_max[resource] - resource_min[resource], 1e-18)
            ),
        }
    artifact = {
        "schema_version": 1,
        "selection_id": "pg5_job_quality_response_span_batch2_v1",
        "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "workload": "job",
        "selection_source": "plan_formula_predictions_only_over_unused_legal_127_subset_endpoints",
        "historical_success_coordinates_used": False,
        "real_measurement_outcomes_used": False,
        "source_plan_only_selection_path": str(FIRST_SELECTION_PATH),
        "source_plan_only_selection_sha256": sha256(FIRST_SELECTION_PATH),
        "source_candidate_count": len(candidates),
        "extreme_frontier_count": len(frontier),
        "extreme_count_per_resource_end": EXTREME_COUNT,
        "feasible_combination_count": feasible_count,
        "selection_constraints": {
            "count": 4,
            "unique_parameter_vectors": True,
            "distinct_structural_fingerprints": True,
            "each_resource_low_and_high_extreme_covered": True,
            "seven_parameter_coverage_priority": "maximize before response-span objective",
        },
        "covered_parameters": sorted(
            set().union(*(set(item["changed_params"]) for item in selected))
        ),
        "selection_objective": {
            "covered_parameter_count": best_coverage,
            "minimum_normalized_resource_span": best[0],
            "sum_normalized_resource_span": best[1],
            "minimum_transformed_pairwise_distance": best[2],
            "sum_distance_from_default": best[3],
        },
        "predicted_span": predicted_span,
        "selected": selected,
        "replacement_queue": replacements,
        "all_predicted_candidates": candidates,
    }
    write_json(OUTPUT_PATH, artifact)
    print(
        json.dumps(
            {
                "status": "selected",
                "selection_path": str(OUTPUT_PATH),
                "selection_sha256": sha256(OUTPUT_PATH),
                "covered_parameters": artifact["covered_parameters"],
                "predicted_span": predicted_span,
                "selected": [
                    {
                        "selection_order": item["selection_order"],
                        "subset_id": item["subset_id"],
                        "rank": item["rank"],
                        "response_roles": item["response_roles"],
                        "changed_params": item["changed_params"],
                        "structural_fingerprint": item["structural_fingerprint"],
                        "params": item["params"],
                        "predicted_formula_resource": item["predicted_formula_resource"],
                    }
                    for item in selected
                ],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
