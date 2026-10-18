#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np


PARAM_NAMES: Tuple[str, ...] = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
PLAN_FEATURE_NAMES: Tuple[str, ...] = (
    "weighted_gather_count",
    "weighted_gather_workers_planned",
    "weighted_parallel_aware_nodes",
)
TARGET_NAMES: Tuple[str, ...] = (
    "db_qps_delta_pct",
    "cpu_delta",
    "memory_delta",
    "io_delta",
)


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if math.isfinite(result) else fallback


def feature_names() -> List[str]:
    return [f"param_log10.{name}" for name in PARAM_NAMES] + [
        f"plan_log1p.{name}" for name in PLAN_FEATURE_NAMES
    ]


def feature_vector(
    params: Dict[str, Any],
    plan_features: Dict[str, Any],
) -> List[float]:
    missing = [name for name in PARAM_NAMES if name not in params]
    if missing:
        raise ValueError(f"missing cost parameters: {missing}")
    values = [
        math.log10(max(_safe_float(params[name], 1e-12), 1e-12))
        for name in PARAM_NAMES
    ]
    values.extend(
        math.log1p(max(0.0, _safe_float(plan_features.get(name), 0.0)))
        for name in PLAN_FEATURE_NAMES
    )
    return values


def target_vector(row: Dict[str, Any]) -> List[float]:
    capability = row.get("capability") or {}
    delta_pp = row.get("resource_delta_pp") or {}
    targets = [_safe_float(row.get("db_qps_delta_pct"), float("nan"))]
    for resource in ("cpu", "memory", "io"):
        targets.append(
            _safe_float(delta_pp.get(resource), float("nan"))
            * _safe_float(capability.get(resource), float("nan"))
            / 100.0
        )
    if any(not math.isfinite(value) for value in targets):
        raise ValueError(f"invalid replay targets for {row.get('candidate_id')}")
    return targets


def pairwise_inversion(
    predicted: Sequence[float],
    actual: Sequence[float],
) -> Dict[str, Any]:
    inversions = 0
    comparable_pairs = 0
    tied_pairs = 0
    for left in range(len(actual)):
        for right in range(left + 1, len(actual)):
            prediction_delta = float(predicted[left]) - float(predicted[right])
            if abs(prediction_delta) <= 1e-12:
                tied_pairs += 1
                continue
            comparable_pairs += 1
            actual_delta = float(actual[left]) - float(actual[right])
            if prediction_delta * actual_delta < 0.0:
                inversions += 1
    return {
        "inversions": inversions,
        "comparable_pairs": comparable_pairs,
        "tied_pairs": tied_pairs,
        "rate": inversions / comparable_pairs if comparable_pairs else 0.0,
    }


def pairwise_inversion_with_actual_margin(
    predicted: Sequence[float],
    actual: Sequence[float],
    margin: float,
) -> Dict[str, Any]:
    inversions = 0
    comparable_pairs = 0
    ignored_pairs = 0
    for left in range(len(actual)):
        for right in range(left + 1, len(actual)):
            actual_delta = float(actual[left]) - float(actual[right])
            if abs(actual_delta) <= margin:
                ignored_pairs += 1
                continue
            prediction_delta = float(predicted[left]) - float(predicted[right])
            if abs(prediction_delta) <= 1e-12:
                ignored_pairs += 1
                continue
            comparable_pairs += 1
            if prediction_delta * actual_delta < 0.0:
                inversions += 1
    return {
        "actual_margin": float(margin),
        "inversions": inversions,
        "comparable_pairs": comparable_pairs,
        "ignored_pairs": ignored_pairs,
        "rate": inversions / comparable_pairs if comparable_pairs else 0.0,
    }


def residual_metrics(
    predicted: np.ndarray,
    actual: np.ndarray,
) -> Dict[str, Dict[str, float]]:
    absolute = np.abs(np.asarray(predicted, dtype=float) - np.asarray(actual, dtype=float))
    return {
        name: {
            "mae": float(np.mean(absolute[:, index])),
            "p90": float(np.quantile(absolute[:, index], 0.90)),
            "p95": float(np.quantile(absolute[:, index], 0.95)),
            "max": float(np.max(absolute[:, index])),
        }
        for index, name in enumerate(TARGET_NAMES)
    }


def serialize_tree(model: Any, leaf_candidates: Dict[int, List[str]]) -> Dict[str, Any]:
    tree = model.tree_
    nodes: List[Dict[str, Any]] = []
    for node_id in range(tree.node_count):
        is_leaf = int(tree.children_left[node_id]) == int(tree.children_right[node_id])
        node = {
            "id": node_id,
            "is_leaf": is_leaf,
            "samples": int(tree.n_node_samples[node_id]),
            "values": [float(value) for value in tree.value[node_id].reshape(-1)],
        }
        if is_leaf:
            node["training_candidates"] = leaf_candidates.get(node_id, [])
        else:
            node.update(
                {
                    "feature_index": int(tree.feature[node_id]),
                    "threshold": float(tree.threshold[node_id]),
                    "left": int(tree.children_left[node_id]),
                    "right": int(tree.children_right[node_id]),
                }
            )
        nodes.append(node)
    return {"root": 0, "nodes": nodes}


def fit_replay_delta_tree(
    rows: Sequence[Dict[str, Any]],
    *,
    max_depth: int = 3,
    min_samples_leaf: int = 1,
    seed: int = 4508333,
) -> Dict[str, Any]:
    from sklearn.model_selection import LeaveOneOut, cross_val_predict
    from sklearn.tree import DecisionTreeRegressor

    if len(rows) < 4:
        raise ValueError("at least four replay rows are required")
    candidate_ids = [str(row.get("candidate_id") or f"row_{index}") for index, row in enumerate(rows)]
    matrix = np.asarray(
        [feature_vector(row.get("params") or {}, row.get("plan_delta_features") or {}) for row in rows],
        dtype=float,
    )
    targets = np.asarray([target_vector(row) for row in rows], dtype=float)
    model = DecisionTreeRegressor(
        max_depth=max(1, int(max_depth)),
        min_samples_leaf=max(1, int(min_samples_leaf)),
        random_state=int(seed),
    )
    model.fit(matrix, targets)
    training_prediction = model.predict(matrix)
    loo_prediction = cross_val_predict(
        DecisionTreeRegressor(
            max_depth=max(1, int(max_depth)),
            min_samples_leaf=max(1, int(min_samples_leaf)),
            random_state=int(seed),
        ),
        matrix,
        targets,
        cv=LeaveOneOut(),
    )
    leaves: Dict[int, List[str]] = {}
    for candidate_id, leaf_id in zip(candidate_ids, model.apply(matrix)):
        leaves.setdefault(int(leaf_id), []).append(candidate_id)
    names = feature_names()
    split_feature_names = sorted(
        {
            names[int(feature_index)]
            for feature_index in model.tree_.feature
            if int(feature_index) >= 0
        }
    )
    return {
        "schema_version": 1,
        "model_type": "multioutput_decision_tree_regressor",
        "seed": int(seed),
        "max_depth": max(1, int(max_depth)),
        "min_samples_leaf": max(1, int(min_samples_leaf)),
        "training_row_count": len(rows),
        "training_candidates": candidate_ids,
        "feature_names": names,
        "split_feature_names": split_feature_names,
        "target_names": list(TARGET_NAMES),
        "feature_support": {
            name: {
                "min": float(np.min(matrix[:, index])),
                "max": float(np.max(matrix[:, index])),
            }
            for index, name in enumerate(names)
        },
        "training_metrics": {
            "absolute_residual": residual_metrics(training_prediction, targets),
            "mae": {
                name: values["mae"]
                for name, values in residual_metrics(training_prediction, targets).items()
            },
            "qps_pairwise": pairwise_inversion(training_prediction[:, 0], targets[:, 0]),
        },
        "leave_one_out_metrics": {
            "absolute_residual": residual_metrics(loo_prediction, targets),
            "mae": {
                name: values["mae"]
                for name, values in residual_metrics(loo_prediction, targets).items()
            },
            "qps_pairwise": pairwise_inversion(loo_prediction[:, 0], targets[:, 0]),
        },
        "tree": serialize_tree(model, leaves),
    }


def fit_replay_delta_knn(
    rows: Sequence[Dict[str, Any]],
    *,
    n_neighbors: int = 5,
) -> Dict[str, Any]:
    from sklearn.neighbors import KNeighborsRegressor
    from sklearn.preprocessing import StandardScaler

    if len(rows) < 4:
        raise ValueError("at least four replay rows are required")
    neighbors = max(1, min(int(n_neighbors), len(rows) - 1))
    candidate_ids = [str(row.get("candidate_id") or f"row_{index}") for index, row in enumerate(rows)]
    matrix = np.asarray(
        [feature_vector(row.get("params") or {}, row.get("plan_delta_features") or {}) for row in rows],
        dtype=float,
    )
    targets = np.asarray([target_vector(row) for row in rows], dtype=float)
    scaler = StandardScaler()
    scaled_matrix = scaler.fit_transform(matrix)
    model = KNeighborsRegressor(n_neighbors=neighbors, weights="distance")
    model.fit(scaled_matrix, targets)
    training_prediction = model.predict(scaled_matrix)
    baseline_indices = [
        index
        for index, candidate_id in enumerate(candidate_ids)
        if candidate_id.startswith("__baseline")
    ]
    loo_prediction = np.zeros_like(targets)
    for holdout_index in range(len(rows)):
        if holdout_index in baseline_indices:
            # The baseline is measured at the start of every outer-loop round;
            # it is a fixed calibration anchor, not an unknown candidate.
            loo_prediction[holdout_index] = targets[holdout_index]
            continue
        train_indices = [index for index in range(len(rows)) if index != holdout_index]
        fold_scaler = StandardScaler()
        fold_matrix = fold_scaler.fit_transform(matrix[train_indices])
        fold_neighbors = max(1, min(neighbors, len(train_indices)))
        fold_model = KNeighborsRegressor(n_neighbors=fold_neighbors, weights="distance")
        fold_model.fit(fold_matrix, targets[train_indices])
        loo_prediction[holdout_index] = fold_model.predict(
            fold_scaler.transform(matrix[holdout_index : holdout_index + 1])
        )[0]
    names = feature_names()
    training_residual = residual_metrics(training_prediction, targets)
    loo_residual = residual_metrics(loo_prediction, targets)
    return {
        "schema_version": 1,
        "model_type": "multioutput_standardized_knn_regressor",
        "n_neighbors": neighbors,
        "training_row_count": len(rows),
        "training_candidates": candidate_ids,
        "feature_names": names,
        "split_feature_names": names,
        "target_names": list(TARGET_NAMES),
        "feature_support": {
            name: {
                "min": float(np.min(matrix[:, index])),
                "max": float(np.max(matrix[:, index])),
            }
            for index, name in enumerate(names)
        },
        "scaler_mean": [float(value) for value in scaler.mean_],
        "scaler_scale": [float(value) for value in scaler.scale_],
        "scaled_training_matrix": scaled_matrix.tolist(),
        "training_targets": targets.tolist(),
        "validation_protocol": {
            "name": "leave_one_candidate_out_with_mandatory_baseline_anchor",
            "baseline_indices": baseline_indices,
            "baseline_candidates": [candidate_ids[index] for index in baseline_indices],
        },
        "training_metrics": {
            "absolute_residual": training_residual,
            "mae": {name: values["mae"] for name, values in training_residual.items()},
            "qps_pairwise": pairwise_inversion(training_prediction[:, 0], targets[:, 0]),
            "qps_pairwise_error_aware_10pp": pairwise_inversion_with_actual_margin(
                training_prediction[:, 0], targets[:, 0], 10.0
            ),
        },
        "leave_one_out_metrics": {
            "absolute_residual": loo_residual,
            "mae": {name: values["mae"] for name, values in loo_residual.items()},
            "qps_pairwise": pairwise_inversion(loo_prediction[:, 0], targets[:, 0]),
            "qps_pairwise_error_aware_10pp": pairwise_inversion_with_actual_margin(
                loo_prediction[:, 0], targets[:, 0], 10.0
            ),
        },
    }


def fit_replay_delta_forest(
    rows: Sequence[Dict[str, Any]],
    *,
    n_estimators: int = 500,
    max_depth: int = 4,
    max_features: float = 0.8,
    seed: int = 4508333,
) -> Dict[str, Any]:
    from sklearn.ensemble import RandomForestRegressor

    if len(rows) < 4:
        raise ValueError("at least four replay rows are required")
    candidate_ids = [str(row.get("candidate_id") or f"row_{index}") for index, row in enumerate(rows)]
    baseline_indices = [
        index for index, candidate_id in enumerate(candidate_ids) if candidate_id.startswith("__baseline")
    ]
    if not baseline_indices:
        raise ValueError("anchored replay-delta forest requires a measured baseline row")
    matrix = np.asarray(
        [feature_vector(row.get("params") or {}, row.get("plan_delta_features") or {}) for row in rows],
        dtype=float,
    )
    targets = np.asarray([target_vector(row) for row in rows], dtype=float)
    estimator_count = max(16, int(n_estimators))
    depth = max(1, int(max_depth))

    def build_model() -> Any:
        return RandomForestRegressor(
            n_estimators=estimator_count,
            max_depth=depth,
            max_features=float(max_features),
            random_state=int(seed),
            n_jobs=1,
        )

    model = build_model()
    model.fit(matrix, targets)
    baseline_raw_prediction = np.mean(model.predict(matrix[baseline_indices]), axis=0)
    baseline_target = np.mean(targets[baseline_indices], axis=0)
    training_prediction = model.predict(matrix) - baseline_raw_prediction + baseline_target
    loo_prediction = np.zeros_like(targets)
    for holdout_index in range(len(rows)):
        if holdout_index in baseline_indices:
            loo_prediction[holdout_index] = targets[holdout_index]
            continue
        train_indices = [index for index in range(len(rows)) if index != holdout_index]
        fold_model = build_model()
        fold_model.fit(matrix[train_indices], targets[train_indices])
        raw_prediction = fold_model.predict(matrix[holdout_index : holdout_index + 1])[0]
        fold_baseline_prediction = np.mean(fold_model.predict(matrix[baseline_indices]), axis=0)
        loo_prediction[holdout_index] = raw_prediction - fold_baseline_prediction + baseline_target

    names = feature_names()
    split_feature_names = sorted(
        {
            names[int(feature_index)]
            for estimator in model.estimators_
            for feature_index in estimator.tree_.feature
            if int(feature_index) >= 0
        }
    )
    training_residual = residual_metrics(training_prediction, targets)
    loo_residual = residual_metrics(loo_prediction, targets)
    return {
        "schema_version": 1,
        "model_type": "multioutput_anchored_random_forest_regressor",
        "seed": int(seed),
        "n_estimators": estimator_count,
        "max_depth": depth,
        "max_features": float(max_features),
        "training_row_count": len(rows),
        "training_candidates": candidate_ids,
        "feature_names": names,
        "split_feature_names": split_feature_names,
        "target_names": list(TARGET_NAMES),
        "feature_support": {
            name: {
                "min": float(np.min(matrix[:, index])),
                "max": float(np.max(matrix[:, index])),
            }
            for index, name in enumerate(names)
        },
        "baseline_anchor": {
            "indices": baseline_indices,
            "candidates": [candidate_ids[index] for index in baseline_indices],
            "feature_vectors": matrix[baseline_indices].tolist(),
            "raw_prediction": baseline_raw_prediction.tolist(),
            "target": baseline_target.tolist(),
        },
        "validation_protocol": {
            "name": "leave_one_candidate_out_with_mandatory_baseline_anchor_and_output_recentering",
            "baseline_indices": baseline_indices,
            "baseline_candidates": [candidate_ids[index] for index in baseline_indices],
        },
        "training_metrics": {
            "absolute_residual": training_residual,
            "mae": {name: values["mae"] for name, values in training_residual.items()},
            "qps_pairwise": pairwise_inversion(training_prediction[:, 0], targets[:, 0]),
            "qps_pairwise_error_aware_10pp": pairwise_inversion_with_actual_margin(
                training_prediction[:, 0], targets[:, 0], 10.0
            ),
        },
        "leave_one_out_metrics": {
            "absolute_residual": loo_residual,
            "mae": {name: values["mae"] for name, values in loo_residual.items()},
            "qps_pairwise": pairwise_inversion(loo_prediction[:, 0], targets[:, 0]),
            "qps_pairwise_error_aware_10pp": pairwise_inversion_with_actual_margin(
                loo_prediction[:, 0], targets[:, 0], 10.0
            ),
        },
        "forest": {
            "trees": [serialize_tree(estimator, {}) for estimator in model.estimators_],
        },
    }


def predict_replay_delta_tree(
    artifact: Dict[str, Any],
    params: Dict[str, Any],
    plan_features: Dict[str, Any],
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    model_type = artifact.get("model_type")
    if model_type not in {
        "multioutput_decision_tree_regressor",
        "multioutput_standardized_knn_regressor",
        "multioutput_anchored_random_forest_regressor",
    }:
        raise ValueError("unsupported replay delta model")
    names = artifact.get("feature_names") or []
    if names != feature_names():
        raise ValueError("replay delta feature schema mismatch")
    values = feature_vector(params, plan_features)
    support = artifact.get("feature_support") or {}
    out_of_support = []
    for index, name in enumerate(names):
        bounds = support.get(name) or {}
        lower = _safe_float(bounds.get("min"), float("-inf"))
        upper = _safe_float(bounds.get("max"), float("inf"))
        if values[index] < lower - 1e-12 or values[index] > upper + 1e-12:
            out_of_support.append(name)
    split_features = set(artifact.get("split_feature_names") or [])
    out_of_support_split_features = [
        name for name in out_of_support if name in split_features
    ]
    if model_type == "multioutput_anchored_random_forest_regressor":
        target_names = artifact.get("target_names") or []
        tree_predictions: List[List[float]] = []
        for serialized in (artifact.get("forest") or {}).get("trees", []):
            nodes = {int(node["id"]): node for node in serialized.get("nodes", [])}
            node_id = int(serialized.get("root", 0))
            while True:
                node = nodes.get(node_id)
                if node is None:
                    raise ValueError(f"missing replay delta forest node {node_id}")
                if node.get("is_leaf"):
                    raw_targets = node.get("values") or []
                    if len(raw_targets) != len(target_names):
                        raise ValueError("replay delta forest target shape mismatch")
                    tree_predictions.append([float(value) for value in raw_targets])
                    break
                feature_index = int(node["feature_index"])
                node_id = int(
                    node["left"]
                    if values[feature_index] <= float(node["threshold"])
                    else node["right"]
                )
        if not tree_predictions:
            raise ValueError("replay delta forest has no trees")
        raw_prediction = np.mean(np.asarray(tree_predictions, dtype=float), axis=0)
        baseline_anchor = artifact.get("baseline_anchor") or {}
        baseline_raw = np.asarray(baseline_anchor.get("raw_prediction") or [], dtype=float)
        baseline_target = np.asarray(baseline_anchor.get("target") or [], dtype=float)
        if baseline_raw.shape != raw_prediction.shape or baseline_target.shape != raw_prediction.shape:
            raise ValueError("invalid replay delta forest baseline anchor shape")
        prediction = raw_prediction - baseline_raw + baseline_target
        return (
            {name: float(value) for name, value in zip(target_names, prediction)},
            {
                "leaf_id": None,
                "path": [],
                "leaf_samples": int(artifact.get("training_row_count") or 0),
                "training_candidates": [],
                "forest_tree_count": len(tree_predictions),
                "out_of_support_features": out_of_support,
                "out_of_support_split_features": out_of_support_split_features,
            },
        )
    if model_type == "multioutput_standardized_knn_regressor":
        mean = np.asarray(artifact.get("scaler_mean") or [], dtype=float)
        scale = np.asarray(artifact.get("scaler_scale") or [], dtype=float)
        training_matrix = np.asarray(artifact.get("scaled_training_matrix") or [], dtype=float)
        training_targets = np.asarray(artifact.get("training_targets") or [], dtype=float)
        if mean.shape != scale.shape or mean.shape != (len(values),):
            raise ValueError("invalid replay delta KNN scaler shape")
        if training_matrix.ndim != 2 or training_matrix.shape[1] != len(values):
            raise ValueError("invalid replay delta KNN feature shape")
        if training_targets.ndim != 2 or training_targets.shape[0] != training_matrix.shape[0]:
            raise ValueError("invalid replay delta KNN target shape")
        safe_scale = np.where(np.abs(scale) <= 1e-12, 1.0, scale)
        scaled_values = (np.asarray(values, dtype=float) - mean) / safe_scale
        distances = np.linalg.norm(training_matrix - scaled_values, axis=1)
        neighbors = max(1, min(int(artifact.get("n_neighbors") or 1), len(distances)))
        neighbor_indices = np.argsort(distances, kind="stable")[:neighbors]
        neighbor_distances = distances[neighbor_indices]
        exact = neighbor_distances <= 1e-12
        if np.any(exact):
            used_indices = neighbor_indices[exact]
            prediction = np.mean(training_targets[used_indices], axis=0)
            weights = np.full(len(used_indices), 1.0 / len(used_indices), dtype=float)
        else:
            used_indices = neighbor_indices
            inverse = 1.0 / np.maximum(neighbor_distances, 1e-12)
            weights = inverse / np.sum(inverse)
            prediction = np.sum(training_targets[used_indices] * weights[:, None], axis=0)
        target_names = artifact.get("target_names") or []
        if len(prediction) != len(target_names):
            raise ValueError("replay delta target shape mismatch")
        candidate_ids = artifact.get("training_candidates") or []
        return (
            {name: float(value) for name, value in zip(target_names, prediction)},
            {
                "leaf_id": None,
                "path": [],
                "leaf_samples": len(used_indices),
                "training_candidates": [candidate_ids[int(index)] for index in used_indices],
                "neighbor_distances": [float(distances[int(index)]) for index in used_indices],
                "neighbor_weights": [float(value) for value in weights],
                "out_of_support_features": out_of_support,
                "out_of_support_split_features": out_of_support_split_features,
            },
        )
    nodes = {int(node["id"]): node for node in (artifact.get("tree") or {}).get("nodes", [])}
    node_id = int((artifact.get("tree") or {}).get("root", 0))
    path: List[int] = []
    while True:
        node = nodes.get(node_id)
        if node is None:
            raise ValueError(f"missing replay delta tree node {node_id}")
        path.append(node_id)
        if node.get("is_leaf"):
            raw_targets = node.get("values") or []
            target_names = artifact.get("target_names") or []
            if len(raw_targets) != len(target_names):
                raise ValueError("replay delta target shape mismatch")
            return (
                {name: float(value) for name, value in zip(target_names, raw_targets)},
                {
                    "leaf_id": node_id,
                    "path": path,
                    "leaf_samples": int(node.get("samples") or 0),
                    "training_candidates": node.get("training_candidates") or [],
                    "out_of_support_features": out_of_support,
                    "out_of_support_split_features": out_of_support_split_features,
                },
            )
        feature_index = int(node["feature_index"])
        node_id = int(node["left"] if values[feature_index] <= float(node["threshold"]) else node["right"])


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Fit a JSON replay-delta model from real Bonect replay rows.")
    parser.add_argument("--replay-jsonl", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--min-samples-leaf", type=int, default=1)
    parser.add_argument("--model-type", choices=("tree", "knn", "forest"), default="tree")
    parser.add_argument("--n-neighbors", type=int, default=5)
    parser.add_argument("--n-estimators", type=int, default=500)
    parser.add_argument("--seed", type=int, default=4508333)
    args = parser.parse_args()
    rows = load_jsonl(Path(args.replay_jsonl))
    if args.model_type == "forest":
        artifact = fit_replay_delta_forest(
            rows,
            n_estimators=args.n_estimators,
            max_depth=args.max_depth,
            seed=args.seed,
        )
    elif args.model_type == "knn":
        artifact = fit_replay_delta_knn(rows, n_neighbors=args.n_neighbors)
    else:
        artifact = fit_replay_delta_tree(
            rows,
            max_depth=args.max_depth,
            min_samples_leaf=args.min_samples_leaf,
            seed=args.seed,
        )
    artifact["source_replay_jsonl"] = str(Path(args.replay_jsonl).resolve())
    output = Path(args.output_json)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    detail = (
        f"nodes={len(artifact['tree']['nodes'])}"
        if artifact["model_type"] == "multioutput_decision_tree_regressor"
        else (
            f"neighbors={artifact['n_neighbors']}"
            if artifact["model_type"] == "multioutput_standardized_knn_regressor"
            else f"trees={artifact['n_estimators']}"
        )
    )
    print(f"wrote replay delta model: {output} rows={artifact['training_row_count']} {detail}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
