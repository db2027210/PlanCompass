#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable

import numpy as np
from sklearn.base import clone
from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import LeaveOneOut, cross_val_predict
from sklearn.multioutput import MultiOutputRegressor
from sklearn.neighbors import KNeighborsRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeRegressor

from bonect_replay_delta_tree import feature_vector, pairwise_inversion, target_vector


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict):
                    yield row


def pairwise_inversion_with_actual_margin(
    predicted: np.ndarray,
    actual: np.ndarray,
    margin: float,
) -> Dict[str, Any]:
    keep = []
    inversions = 0
    for left in range(len(actual)):
        for right in range(left + 1, len(actual)):
            actual_delta = float(actual[left]) - float(actual[right])
            if abs(actual_delta) <= margin:
                continue
            prediction_delta = float(predicted[left]) - float(predicted[right])
            if abs(prediction_delta) <= 1e-12:
                continue
            keep.append((left, right))
            if prediction_delta * actual_delta < 0.0:
                inversions += 1
    return {
        "actual_margin": margin,
        "comparable_pairs": len(keep),
        "inversions": inversions,
        "rate": inversions / len(keep) if keep else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate replay-delta regressors with leave-one-out CV.")
    parser.add_argument("--replay-jsonl", required=True, type=Path)
    args = parser.parse_args()

    rows = list(iter_jsonl(args.replay_jsonl))
    matrix = np.asarray(
        [feature_vector(row.get("params") or {}, row.get("plan_delta_features") or {}) for row in rows],
        dtype=float,
    )
    targets = np.asarray([target_vector(row) for row in rows], dtype=float)
    target_sets = {
        "worker_baseline_delta_pct": targets,
        "absolute_db_qps": targets.copy(),
    }
    target_sets["absolute_db_qps"][:, 0] = np.asarray(
        [float((row.get("db_delta") or {}).get("db_qps")) for row in rows],
        dtype=float,
    )
    baseline_indices = [
        index
        for index, row in enumerate(rows)
        if str(row.get("candidate_id") or "").startswith("__baseline")
    ]
    models = {
        "decision_tree_d4_l1": DecisionTreeRegressor(max_depth=4, random_state=4508333),
        "random_forest": RandomForestRegressor(
            n_estimators=500,
            max_depth=4,
            max_features=0.8,
            random_state=4508333,
            n_jobs=1,
        ),
        "extra_trees": ExtraTreesRegressor(
            n_estimators=500,
            max_depth=5,
            max_features=0.8,
            random_state=4508333,
            n_jobs=1,
        ),
        "gradient_boosting_huber": MultiOutputRegressor(
            GradientBoostingRegressor(
                n_estimators=100,
                max_depth=2,
                learning_rate=0.05,
                loss="huber",
                random_state=4508333,
            )
        ),
    }
    for alpha in (0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0):
        models[f"ridge_alpha{alpha:g}"] = make_pipeline(StandardScaler(), Ridge(alpha=alpha))
    for neighbors in range(2, min(11, len(rows))):
        models[f"knn_distance_{neighbors}"] = make_pipeline(
            StandardScaler(), KNeighborsRegressor(n_neighbors=neighbors, weights="distance")
        )
    for target_mode, target_values in target_sets.items():
        for name, model in models.items():
            if baseline_indices:
                prediction = np.zeros_like(target_values)
                for holdout_index in range(len(rows)):
                    if holdout_index in baseline_indices:
                        prediction[holdout_index] = target_values[holdout_index]
                        continue
                    train_indices = [index for index in range(len(rows)) if index != holdout_index]
                    fold_model = clone(model)
                    fold_model.fit(matrix[train_indices], target_values[train_indices])
                    raw_prediction = fold_model.predict(
                        matrix[holdout_index : holdout_index + 1]
                    )[0]
                    baseline_prediction = np.mean(
                        fold_model.predict(matrix[baseline_indices]),
                        axis=0,
                    )
                    baseline_target = np.mean(target_values[baseline_indices], axis=0)
                    prediction[holdout_index] = (
                        raw_prediction - baseline_prediction + baseline_target
                    )
            else:
                prediction = cross_val_predict(model, matrix, target_values, cv=LeaveOneOut(), n_jobs=1)
            qps_error = np.abs(prediction[:, 0] - target_values[:, 0])
            print(
                json.dumps(
                    {
                    "target_mode": target_mode,
                    "validation_protocol": (
                        "leave_one_candidate_out_with_mandatory_baseline_anchor"
                        if baseline_indices
                        else "leave_one_out"
                    ),
                    "model": name,
                    "qps_pairwise": pairwise_inversion(prediction[:, 0], target_values[:, 0]),
                    "qps_pairwise_margin_10": pairwise_inversion_with_actual_margin(
                        prediction[:, 0], target_values[:, 0], 10.0
                    ),
                    "qps_pairwise_margin_20": pairwise_inversion_with_actual_margin(
                        prediction[:, 0], target_values[:, 0], 20.0
                    ),
                    "qps_pairwise_margin_30": pairwise_inversion_with_actual_margin(
                        prediction[:, 0], target_values[:, 0], 30.0
                    ),
                    "qps_mae": float(np.mean(qps_error)),
                    "qps_p95": float(np.quantile(qps_error, 0.95)),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
