from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import HuberRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler


SEED = 42
TOP_K = 15
MIN_R2 = 0.0
MAX_TOP15_INVERSION = 0.10

PLAN_FEATURES = [
    "F_scan_tuple",
    "F_nl_tuple",
    "F_join_tuple",
    "F_agg_tuple",
    "F_sort_tuple",
    "F_materialize_tuple",
    "F_proj_tuple",
    "F_index_tuple",
    "F_hash_build",
    "F_hash_probe",
    "F_scan_filter",
    "F_index_cond",
    "F_join_filter",
    "F_hash_cond",
    "F_merge_cond",
    "F_agg_trans",
    "F_agg_final",
    "F_sort_cmp",
    "F_materialize_oper",
]


def _discover_files(data_dir: Path) -> List[Tuple[Path, str, int]]:
    result: List[Tuple[Path, str, int]] = []
    pattern = re.compile(r"^(h|c|ch)(\d+)_(serial|5s)_features\.parquet$")
    family_map = {"h": "H_TPCH", "c": "C_TPCC", "ch": "CH_HYBRID"}
    for path in sorted(data_dir.glob("*_features.parquet")):
        match = pattern.match(path.name)
        if match is None:
            continue
        result.append((path, family_map[match.group(1)], int(match.group(2))))
    if not result:
        raise FileNotFoundError(f"No MySQL CPU feature parquet files in {data_dir}")
    return result


def load_case_candidates(data_dir: Path) -> pd.DataFrame:
    """Return one candidate per benchmark case/plan configuration.

    Monitoring-window rows are repetitions of a case, not optimizer candidates.
    Averaging them prevents a repeated plan from appearing in both sides of the
    validation and matches the case-level candidate ranking used by the optimizer.
    """
    rows: List[Dict[str, Any]] = []
    for path, family, case_number in _discover_files(data_dir):
        frame = pd.read_parquet(path)
        required = PLAN_FEATURES + ["target_cpu"]
        missing = [name for name in required if name not in frame.columns]
        if missing:
            raise ValueError(f"{path} is missing columns: {missing}")
        frame = frame.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        frame = frame[frame["target_cpu"].astype(float) > 0.0]
        if frame.empty:
            continue
        row: Dict[str, Any] = {
            name: float(frame[name].astype(float).mean()) for name in PLAN_FEATURES
        }
        row.update(
            {
                "active_sql_count": float(
                    frame["active_sql_count"].astype(float).mean()
                    if "active_sql_count" in frame.columns
                    else 1.0
                ),
                "target_cpu": float(frame["target_cpu"].astype(float).mean()),
                "family": family,
                "case": path.name.split("_", 1)[0],
                "case_number": case_number,
                "source_file": path.name,
                "raw_window_count": int(len(frame)),
            }
        )
        rows.append(row)
    data = pd.DataFrame(rows)
    if len(data) < TOP_K:
        raise RuntimeError(f"Need at least {TOP_K} case candidates, found {len(data)}")
    return data


def build_features(data: pd.DataFrame) -> pd.DataFrame:
    columns = PLAN_FEATURES + ["active_sql_count"]
    features = np.log1p(data[columns].astype(float).clip(lower=0.0))
    features = features.copy()
    features["benchmark_H_TPCH"] = (data["family"] == "H_TPCH").astype(float)
    features["benchmark_C_TPCC"] = (data["family"] == "C_TPCC").astype(float)
    features["benchmark_CH_HYBRID"] = (data["family"] == "CH_HYBRID").astype(float)
    # Case number is benchmark context (scale/concurrency configuration), not a label.
    features["case_number"] = data["case_number"].astype(float)
    features["tpch_scale_index"] = (
        data["case_number"].astype(float) * (data["family"] == "H_TPCH").astype(float)
    )
    return features


def make_model(epsilon: float = 1.5) -> Pipeline:
    # Robust linear regression generalizes smoothly across the scale/concurrency
    # cases and is much less sensitive to the few noisy case aggregates.
    return make_pipeline(
        StandardScaler(),
        HuberRegressor(
            max_iter=10000,
            epsilon=epsilon,
            alpha=0.001,
            tol=1e-7,
        ),
    )


def pairwise_inversion(
    actual: Sequence[float],
    indices: Sequence[int],
) -> Dict[str, Any]:
    actual_array = np.asarray(actual, dtype=float)
    inversions = 0
    pairs = 0
    for left_offset, left in enumerate(indices):
        for right in indices[left_offset + 1 :]:
            actual_delta = actual_array[left] - actual_array[right]
            # Match shell/evaluate_bonect_estimator_ranking.py exactly: only a
            # real-score tie is skipped. Predicted ties retain stable candidate order.
            if abs(actual_delta) <= 1e-12:
                continue
            pairs += 1
            if actual_delta < 0.0:
                inversions += 1
    return {
        "inversions": inversions,
        "comparable_pairs": pairs,
        "pairwise_inversion_rate": inversions / pairs if pairs else None,
    }


def evaluate(data: pd.DataFrame, predicted: np.ndarray) -> Dict[str, Any]:
    actual = data["target_cpu"].astype(float).to_numpy()
    ranked = np.argsort(-predicted, kind="stable")[:TOP_K].tolist()
    ranking = pairwise_inversion(actual, ranked)
    ranking.update(
        {
            "k": TOP_K,
            "labeled_items": len(ranked),
            "direction": "descending_cpu_pressure",
            "candidate_cases": data.iloc[ranked]["case"].astype(str).tolist(),
        }
    )
    rmse = math.sqrt(mean_squared_error(actual, predicted))
    metrics: Dict[str, Any] = {
        "candidate_count": int(len(data)),
        "rmse": float(rmse),
        "mae": float(mean_absolute_error(actual, predicted)),
        "r2": float(r2_score(actual, predicted)),
        "top15": ranking,
    }
    metrics["by_benchmark"] = {}
    for family in sorted(data["family"].astype(str).unique().tolist()):
        mask = data["family"].astype(str).to_numpy() == family
        family_actual = actual[mask]
        family_predicted = predicted[mask]
        metrics["by_benchmark"][family] = {
            "candidate_count": int(mask.sum()),
            "rmse": float(math.sqrt(mean_squared_error(family_actual, family_predicted))),
            "mae": float(mean_absolute_error(family_actual, family_predicted)),
            "r2": float(r2_score(family_actual, family_predicted)),
        }
    metrics["gate"] = {
        "min_r2": MIN_R2,
        "max_top15_pairwise_inversion_rate": MAX_TOP15_INVERSION,
        "r2_passed": metrics["r2"] >= MIN_R2,
        "top15_label_count_passed": ranking["labeled_items"] == TOP_K,
        "top15_ranking_passed": (
            ranking["pairwise_inversion_rate"] is not None
            and ranking["pairwise_inversion_rate"] <= MAX_TOP15_INVERSION
        ),
    }
    metrics["gate"]["passed"] = all(
        metrics["gate"][name]
        for name in ("r2_passed", "top15_label_count_passed", "top15_ranking_passed")
    )
    return metrics


def _oof_predict(model: Any, features: pd.DataFrame, target: np.ndarray) -> np.ndarray:
    folds = KFold(n_splits=5, shuffle=True, random_state=123)
    return np.asarray(
        cross_val_predict(model, features, target, cv=folds, n_jobs=1),
        dtype=float,
    )


def _routed_oof_predict(
    model: Any,
    data: pd.DataFrame,
    features: pd.DataFrame,
    target: np.ndarray,
) -> np.ndarray:
    """Strict OOF prediction with a separate fit for each benchmark family."""
    predicted = np.zeros(len(data), dtype=float)
    families = data["family"].astype(str).to_numpy()
    folds = KFold(n_splits=5, shuffle=True, random_state=123)
    for train_indices, test_indices in folds.split(features):
        for family in sorted(set(families)):
            family_train = train_indices[families[train_indices] == family]
            family_test = test_indices[families[test_indices] == family]
            if not len(family_test):
                continue
            routed_model = clone(model)
            routed_model.fit(features.iloc[family_train], target[family_train])
            predicted[family_test] = routed_model.predict(features.iloc[family_test])
    return predicted


def compare_candidates(
    data: pd.DataFrame,
    features: pd.DataFrame,
    target: np.ndarray,
) -> Tuple[List[Dict[str, Any]], Dict[str, np.ndarray]]:
    """Compare a small, reproducible set of leakage-free OOF candidates."""
    candidate_predictions: Dict[str, np.ndarray] = {}
    for epsilon in (1.35, 1.5, 1.75, 2.0):
        raw_name = f"global_huber_e{epsilon:g}_raw"
        raw_prediction = _oof_predict(make_model(epsilon), features, target)
        candidate_predictions[raw_name] = raw_prediction
        candidate_predictions[f"global_huber_e{epsilon:g}_nonnegative"] = np.clip(
            raw_prediction,
            0.0,
            None,
        )

    for depth in (1, 2):
        model = GradientBoostingRegressor(
            n_estimators=300,
            max_depth=depth,
            loss="huber",
            random_state=SEED,
        )
        prediction = _oof_predict(model, features, target)
        candidate_predictions[f"global_gradient_boosting_d{depth}"] = np.clip(
            prediction,
            0.0,
            None,
        )

    for name, model in (
        (
            "global_extra_trees",
            ExtraTreesRegressor(
                n_estimators=500,
                min_samples_leaf=1,
                random_state=SEED,
                n_jobs=1,
            ),
        ),
        (
            "global_random_forest",
            RandomForestRegressor(
                n_estimators=500,
                min_samples_leaf=1,
                random_state=SEED,
                n_jobs=1,
            ),
        ),
    ):
        candidate_predictions[name] = np.clip(
            _oof_predict(model, features, target),
            0.0,
            None,
        )

    log_model = TransformedTargetRegressor(
        regressor=make_model(1.5),
        func=np.log1p,
        inverse_func=np.expm1,
    )
    candidate_predictions["global_huber_e1.5_log1p_target"] = np.clip(
        _oof_predict(log_model, features, target),
        0.0,
        None,
    )
    candidate_predictions["benchmark_routed_huber_e1.5_nonnegative"] = np.clip(
        _routed_oof_predict(make_model(1.5), data, features, target),
        0.0,
        None,
    )

    comparisons = [
        {"name": name, "strict_oof_metrics": evaluate(data, prediction)}
        for name, prediction in candidate_predictions.items()
    ]
    return comparisons, candidate_predictions


def _metric_delta(selected: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        name: float(selected[name]) - float(baseline[name])
        for name in ("rmse", "mae", "r2")
    }
    result["top15_pairwise_inversion_rate"] = float(
        selected["top15"]["pairwise_inversion_rate"]
    ) - float(baseline["top15"]["pairwise_inversion_rate"])
    result["by_benchmark"] = {
        family: {
            name: float(selected["by_benchmark"][family][name])
            - float(baseline["by_benchmark"][family][name])
            for name in ("rmse", "mae", "r2")
        }
        for family in selected["by_benchmark"]
    }
    return result


def _validate_selected_improvement(
    selected: Dict[str, Any],
    baseline: Dict[str, Any],
) -> None:
    """Prevent a future data refresh from silently publishing a regression."""
    reasons: List[str] = []
    if not selected["gate"]["passed"]:
        reasons.append("selected candidate does not pass the hard quality/ranking gate")
    if selected["r2"] < baseline["r2"] - 0.005:
        reasons.append("overall R2 dropped by more than 0.005")
    for metric in ("rmse", "mae"):
        if selected[metric] > baseline[metric] + 1e-12:
            reasons.append(f"overall {metric} is worse than baseline")
    for family in ("H_TPCH", "CH_HYBRID"):
        for metric in ("rmse", "mae"):
            if (
                selected["by_benchmark"][family][metric]
                > baseline["by_benchmark"][family][metric] + 1e-12
            ):
                reasons.append(f"{family} {metric} is worse than baseline")
    if reasons:
        raise RuntimeError("selected CPU model regressed: " + "; ".join(reasons))


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the MySQL plan-tree CPU estimator")
    parser.add_argument("--data-dir", type=Path, default=Path("/disk3/anonymous/features_mysql"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "artifacts",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    data = load_case_candidates(args.data_dir)
    features = build_features(data)
    target = data["target_cpu"].astype(float).to_numpy()
    comparisons, candidate_predictions = compare_candidates(data, features, target)
    comparison_map = {
        item["name"]: item["strict_oof_metrics"] for item in comparisons
    }
    baseline_name = "global_huber_e1.5_raw"
    selected_name = "global_huber_e1.5_nonnegative"
    baseline_metrics = comparison_map[baseline_name]
    metrics = comparison_map[selected_name]
    oof = candidate_predictions[selected_name]
    _validate_selected_improvement(metrics, baseline_metrics)

    model = make_model()
    model.fit(features, target)
    artifact = {
        "model": model,
        "features": list(features.columns),
        "plan_features": PLAN_FEATURES,
        "prediction_transform": {"type": "clip_min", "clip_min": 0.0},
        "candidate_unit": "benchmark_case_plan_configuration",
        "target": "mean_cpu_seconds_per_benchmark_observation",
        "benchmark_families": sorted(data["family"].unique().tolist()),
        "seed": SEED,
        "validation": "5_fold_out_of_fold_case_candidates",
        "metrics": metrics,
        "baseline_metrics": baseline_metrics,
        "selected_candidate": selected_name,
    }
    joblib.dump(artifact, args.output_dir / "mysql_cpu_plan_model.joblib")

    evidence = data[["case", "family", "source_file", "raw_window_count", "target_cpu"]].copy()
    evidence["oof_prediction"] = oof
    evidence.to_csv(args.output_dir / "mysql_cpu_oof_predictions.csv", index=False)

    report = {
        "status": "passed" if metrics["gate"]["passed"] else "failed",
        "data_dir": str(args.data_dir),
        "model": "StandardScaler + HuberRegressor(epsilon=1.5, alpha=0.001) + clip_min(0)",
        "selected_candidate": selected_name,
        "baseline_candidate": baseline_name,
        "selection_policy": {
            "validation": "same fixed case-level 5-fold OOF for every candidate",
            "hard_gates": "R2 >= baseline-0.005 and exact Top15 inversion <= 0.10",
            "priority": "overall/H/CH RMSE and MAE; preserve H before improving CH",
            "label_postprocessing": False,
        },
        "feature_count": len(features.columns),
        "family_candidate_counts": {
            str(name): int(count) for name, count in data.groupby("family").size().items()
        },
        "metrics": metrics,
        "baseline_metrics": baseline_metrics,
        "selected_minus_baseline": _metric_delta(metrics, baseline_metrics),
        "candidate_comparison": comparisons,
        "artifact": str(args.output_dir / "mysql_cpu_plan_model.joblib"),
        "oof_predictions": str(args.output_dir / "mysql_cpu_oof_predictions.csv"),
    }
    (args.output_dir / "mysql_cpu_evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if not metrics["gate"]["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
