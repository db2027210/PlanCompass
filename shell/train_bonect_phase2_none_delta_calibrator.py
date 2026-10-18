#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_DIR = ROOT / "report/bonect/none_delta_cal_v78_20260711"
MANIFEST_PATH = CAMPAIGN_DIR / "manifest.json"
STATE_PATH = CAMPAIGN_DIR / "state.jsonl"
OUTPUT_PATH = CAMPAIGN_DIR / "delta_calibrator.json"

COST_PARAM_KEYS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
TARGETS = (
    "db_qps_delta_pct",
    "cpu_delta_pct",
    "memory_delta_pct",
    "io_delta_pct",
)

PREFERRED_PLAN_FEATURES = (
    "plan_gather_count_share",
    "plan_parallel_aware_nodes_share",
    "plan_workers_planned",
    "plan_seq_scan_count_share",
    "plan_index_scan_count_share",
    "plan_bitmap_heap_scan_count_share",
    "plan_hash_join_count_share",
    "plan_nested_loop_count_share",
    "plan_merge_join_count_share",
    "plan_hashagg_count_share",
    "plan_sort_count_share",
    "plan_materialize_count_share",
    "plan_random_vs_seq_io_proxy",
    "io_spill_pressure",
    "io_index_pressure",
    "io_join_pressure",
    "io_sort_spill_ratio",
    "io_seq_scan_share",
    "io_idx_scan_ratio",
    "io_nl_ratio",
    "io_hj_ratio",
)

FALLBACK_PLAN_FEATURES = {
    "io_ratio_run_vs_total_cost",
    "io_spill_density",
    "io_index_selectivity",
    "io_seq_scan_ratio",
    "plan_rows_per_node",
}


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def record_is_eligible(record: Dict[str, Any]) -> bool:
    if not record.get("plan_features") or safe_float(record.get("db_qps"), 0.0) <= 0.0:
        return False
    if not all(
        safe_float((record.get("resource_normalized") or {}).get(name), 0.0) > 0.0
        for name in ("cpu", "memory", "io")
    ):
        return False
    kind = str(record.get("kind") or "")
    if kind == "current_feature_baseline":
        return True
    if not record.get("paired_baseline_valid"):
        return False
    return True


def labels_for_record(
    record: Dict[str, Any],
    *,
    fallback_db_qps: float,
    fallback_resource: Dict[str, Any],
    fallback_run_id: str,
) -> Tuple[List[float], str]:
    paired_baseline = (
        record.get("paired_baseline")
        if record.get("paired_baseline_valid")
        else {}
    ) or {}
    record_baseline_resource = (
        paired_baseline.get("resource_normalized") or fallback_resource
    )
    record_baseline_db_qps = safe_float(
        paired_baseline.get("db_qps"),
        fallback_db_qps,
    )
    resource = record.get("resource_normalized") or {}
    labels = [
        delta_pct(safe_float(record.get("db_qps")), record_baseline_db_qps),
        delta_pct(
            safe_float(resource.get("cpu")),
            safe_float(record_baseline_resource.get("cpu")),
        ),
        delta_pct(
            safe_float(resource.get("memory")),
            safe_float(record_baseline_resource.get("memory")),
        ),
        delta_pct(
            safe_float(resource.get("io")),
            safe_float(record_baseline_resource.get("io")),
        ),
    ]
    baseline_run_id = str(paired_baseline.get("run_id") or fallback_run_id)
    return labels, baseline_run_id


def completed_records(path: Path) -> List[Dict[str, Any]]:
    latest: Dict[int, Dict[str, Any]] = {}
    latest_current_baseline: Dict[str, Any] = {}
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("event") != "calibration_point_complete":
                continue
            record = dict(record)
            kind = str(record.get("kind") or "")
            if kind == "current_feature_baseline":
                latest_current_baseline = record
            elif kind == "current_anchor_repeat" and latest_current_baseline:
                try:
                    baseline_ts = datetime.fromisoformat(
                        str(latest_current_baseline.get("timestamp") or "")
                    )
                    candidate_ts = datetime.fromisoformat(
                        str(record.get("timestamp") or "")
                    )
                    elapsed_seconds = (candidate_ts - baseline_ts).total_seconds()
                except ValueError:
                    elapsed_seconds = -1.0
                same_campaign = (
                    record.get("campaign_id")
                    == latest_current_baseline.get("campaign_id")
                )
                if same_campaign and 0.0 <= elapsed_seconds <= 1800.0:
                    record["paired_baseline_valid"] = True
                    record["paired_baseline_position"] = "before"
                    record["paired_baseline"] = {
                        key: latest_current_baseline.get(key)
                        for key in (
                            "run_id",
                            "history_path",
                            "db_qps",
                            "db_xact_tps",
                            "resource_normalized",
                            "utilization_pct",
                            "resource_sources",
                            "stable_sample_count",
                            "plan_features",
                            "plan_feature_count",
                            "plan_feature_signature",
                        )
                    }
            latest[int(record.get("point_index", -1))] = record
    return [latest[index] for index in sorted(latest) if index >= 0]


def delta_pct(value: float, baseline: float) -> float:
    return 100.0 * (value - baseline) / baseline if baseline else 0.0


def pairwise_inversion_rate(actual: Sequence[float], predicted: Sequence[float]) -> float:
    inversions = 0
    comparable = 0
    for left in range(len(actual)):
        for right in range(left + 1, len(actual)):
            actual_order = actual[left] - actual[right]
            predicted_order = predicted[left] - predicted[right]
            if abs(actual_order) <= 1e-9:
                continue
            comparable += 1
            if actual_order * predicted_order <= 0.0:
                inversions += 1
    return inversions / comparable if comparable else 1.0


def direction_accuracy(actual: Sequence[float], predicted: Sequence[float], epsilon: float = 0.5) -> float:
    matched = 0
    count = 0
    for actual_value, predicted_value in zip(actual, predicted):
        if abs(actual_value) < epsilon:
            continue
        count += 1
        if actual_value * predicted_value > 0.0:
            matched += 1
    return matched / count if count else 0.0


def rbf_predict(x_train: np.ndarray, y_train: np.ndarray, x: np.ndarray, bandwidth: float) -> np.ndarray:
    distance_sq = np.sum((x_train - x[None, :]) ** 2, axis=1)
    weights = np.exp(-distance_sq / (2.0 * bandwidth ** 2))
    if float(np.sum(weights)) <= 1e-12:
        weights = np.zeros_like(weights)
        weights[int(np.argmin(distance_sq))] = 1.0
    else:
        weights = weights / float(np.sum(weights))
    return np.sum(weights[:, None] * y_train, axis=0)


def loo_predictions(x: np.ndarray, y: np.ndarray, bandwidth: float) -> np.ndarray:
    predictions = []
    for index in range(len(x)):
        mask = np.arange(len(x)) != index
        predictions.append(rbf_predict(x[mask], y[mask], x[index], bandwidth))
    return np.asarray(predictions, dtype=float)


def select_plan_columns(
    records: Sequence[Dict[str, Any]],
    baseline_plan: Dict[str, Any],
    max_features: int,
) -> List[Tuple[str, np.ndarray, Dict[str, Any]]]:
    """Select varying plan semantics without consulting target labels."""
    if not records or max_features <= 0:
        return []
    common_names = sorted(
        set.intersection(
            *(set((record.get("plan_features") or {}).keys()) for record in records)
        )
    )
    preferred_rank = {
        name: index for index, name in enumerate(PREFERRED_PLAN_FEATURES)
    }
    candidates: List[Tuple[Tuple[Any, ...], str, np.ndarray, Dict[str, Any]]] = []
    for name in common_names:
        values = np.asarray(
            [
                safe_float(
                    (record.get("plan_features") or {}).get(name),
                    safe_float(
                        (
                            (record.get("paired_baseline") or {}).get(
                                "plan_features"
                            )
                            or baseline_plan
                        ).get(name)
                    ),
                )
                - safe_float(
                    (
                        (record.get("paired_baseline") or {}).get("plan_features")
                        or baseline_plan
                    ).get(name)
                )
                for record in records
            ],
            dtype=float,
        )
        scale = float(np.std(values))
        if scale <= 1e-12:
            continue
        nonzero_fraction = float(np.mean(np.abs(values) > 1e-12))
        unique_count = len({round(float(value / scale), 8) for value in values})
        if name in preferred_rank:
            priority: Tuple[Any, ...] = (0, preferred_rank[name], name)
            selection_group = "preferred_semantic"
        elif (
            (name.startswith("plan_") and name.endswith("_share"))
            or name in FALLBACK_PLAN_FEATURES
        ):
            priority = (1, -nonzero_fraction, -unique_count, name)
            selection_group = "semantic_fallback"
        else:
            continue
        candidates.append(
            (
                priority,
                name,
                values,
                {
                    "selection_group": selection_group,
                    "std": scale,
                    "nonzero_fraction": nonzero_fraction,
                    "unique_count": unique_count,
                },
            )
        )
    candidates.sort(key=lambda item: item[0])
    return [
        (name, values, metadata)
        for _priority, name, values, metadata in candidates[:max_features]
    ]


def nearest_neighbor_distances(x: np.ndarray) -> np.ndarray:
    if len(x) <= 1:
        return np.asarray([], dtype=float)
    result = []
    for index in range(len(x)):
        distances = np.sqrt(
            np.sum((x[np.arange(len(x)) != index] - x[index]) ** 2, axis=1)
        )
        result.append(float(np.min(distances)))
    return np.asarray(result, dtype=float)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train fixed-workload Phase2 None delta calibrator.")
    parser.add_argument("--min-samples", type=int, default=10)
    parser.add_argument("--max-plan-features", type=int, default=16)
    args = parser.parse_args()

    manifest = read_json(MANIFEST_PATH)
    records = completed_records(STATE_PATH)
    baseline_records = [
        record for record in records if record.get("kind") == "current_feature_baseline"
    ]
    if not baseline_records:
        raise RuntimeError("current_feature_baseline is required before delta training")
    baseline = baseline_records[-1]
    baseline_params = baseline.get("params") or {}
    baseline_plan = baseline.get("plan_features") or {}
    baseline_resource = baseline.get("resource_normalized") or {}
    baseline_db_qps = safe_float(baseline.get("db_qps"), 0.0)
    if not baseline_plan:
        raise RuntimeError("current_feature_baseline has no aggregate plan features")

    usable = [record for record in records if record_is_eligible(record)]
    if len(usable) < max(3, args.min_samples):
        raise RuntimeError(f"insufficient feature-bearing calibration samples: {len(usable)}")

    labels: List[List[float]] = []
    param_rows: List[List[float]] = []
    label_baseline_run_ids: List[str] = []
    for record in usable:
        params = record.get("params") or {}
        param_rows.append([
            math.log(max(safe_float(params.get(name)), 1e-12) / max(safe_float(baseline_params.get(name)), 1e-12))
            for name in COST_PARAM_KEYS
        ])
        record_labels, label_baseline_run_id = labels_for_record(
            record,
            fallback_db_qps=baseline_db_qps,
            fallback_resource=baseline_resource,
            fallback_run_id=str(baseline.get("run_id") or ""),
        )
        labels.append(record_labels)
        label_baseline_run_ids.append(label_baseline_run_id)
    y = np.asarray(labels, dtype=float)

    plan_columns = select_plan_columns(
        usable,
        baseline_plan,
        max(0, args.max_plan_features),
    )

    raw_columns: List[np.ndarray] = []
    feature_names: List[str] = []
    for index, name in enumerate(COST_PARAM_KEYS):
        values = np.asarray([row[index] for row in param_rows], dtype=float)
        if float(np.std(values)) <= 1e-12:
            continue
        raw_columns.append(values)
        feature_names.append(f"param_log:{name}")
    for name, values, _metadata in plan_columns:
        raw_columns.append(values)
        feature_names.append(f"plan_delta:{name}")
    raw_x = np.column_stack(raw_columns)
    scales = np.maximum(np.std(raw_x, axis=0), 1e-12)
    x = raw_x / scales[None, :]

    candidates = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
    evaluations: List[Dict[str, Any]] = []
    for bandwidth in candidates:
        predicted = loo_predictions(x, y, bandwidth)
        inversion = pairwise_inversion_rate(y[:, 0], predicted[:, 0])
        direction = {
            TARGETS[index]: direction_accuracy(y[:, index], predicted[:, index])
            for index in range(1, len(TARGETS))
        }
        normalized_mae = float(np.mean(np.abs(predicted - y) / np.maximum(np.std(y, axis=0), 1.0)))
        objective = inversion + float(np.mean([1.0 - value for value in direction.values()])) + 0.05 * normalized_mae
        evaluations.append({
            "bandwidth": bandwidth,
            "objective": objective,
            "pairwise_inversion_rate": inversion,
            "direction_accuracy": direction,
            "normalized_mae": normalized_mae,
            "loo_predictions": predicted.tolist(),
        })
    selected = min(evaluations, key=lambda item: item["objective"])
    neighbor_distances = nearest_neighbor_distances(x)
    neighbor_distance_p95 = (
        float(np.quantile(neighbor_distances, 0.95))
        if len(neighbor_distances)
        else 0.0
    )
    max_supported_distance = max(1e-6, 1.5 * neighbor_distance_p95)
    direction_values = list((selected.get("direction_accuracy") or {}).values())
    gate_reasons = []
    if selected["pairwise_inversion_rate"] > 0.35:
        gate_reasons.append("db_qps_pairwise_inversion_above_0.35")
    if direction_values and min(direction_values) < 0.60:
        gate_reasons.append("resource_direction_accuracy_below_0.60")

    points = []
    for record, vector, label, label_baseline_run_id in zip(
        usable,
        x.tolist(),
        labels,
        label_baseline_run_ids,
    ):
        points.append({
            "point_index": record.get("point_index"),
            "point_name": record.get("point_name"),
            "params": record.get("params") or {},
            "x": vector,
            "label_baseline_run_id": label_baseline_run_id,
            "paired_baseline_valid": bool(record.get("paired_baseline_valid")),
            "labels": {name: value for name, value in zip(TARGETS, label)},
        })
    artifact = {
        "version": "phase2_fixed_workload_real_delta_rbf_v1",
        "campaign_id": manifest.get("campaign_id"),
        "sample_count": len(usable),
        "feature_names": feature_names,
        "feature_scales": scales.tolist(),
        "plan_feature_selection": {
            "method": "target_independent_semantic_variation_v1",
            "selected": [
                {"name": name, **metadata}
                for name, _values, metadata in plan_columns
            ],
            "target_labels_used_for_selection": False,
        },
        "baseline_params": baseline_params,
        "baseline_plan_features": baseline_plan,
        "baseline_resource": baseline_resource,
        "baseline_db_qps": baseline_db_qps,
        "bandwidth": selected["bandwidth"],
        "support": {
            "method": "training_nearest_neighbor_p95_x_1.5",
            "training_nearest_distance_p95": neighbor_distance_p95,
            "max_nearest_distance": max_supported_distance,
            "training_nearest_distances": neighbor_distances.tolist(),
        },
        "validation": selected,
        "bandwidth_evaluations": evaluations,
        "training_gate": {
            "passed": not gate_reasons,
            "reasons": gate_reasons,
            "note": "Top15 real replay still requires pairwise inversion <= 0.10 before full-grid.",
        },
        "points": points,
    }
    OUTPUT_PATH.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT_PATH)
    print(json.dumps(artifact["training_gate"], ensure_ascii=False))
    return 0 if not gate_reasons else 2


if __name__ == "__main__":
    raise SystemExit(main())
