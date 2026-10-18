#!/usr/bin/env python3
"""Train and audit benchmark-routed MySQL plan-only total-IO models.

Formal acceptance uses non-overlapping replica cross-fit and case-level
aggregation, matching the optimizer candidate semantics.  A stricter
leave-one-case-out diagnostic is reported separately and is never presented as
the formal gate result.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import (
    ExtraTreesRegressor,
    GradientBoostingRegressor,
    HistGradientBoostingRegressor,
    RandomForestRegressor,
)
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


DEFAULT_DATA_DIR = Path("/home/anonymous/program/features_cache_M1/mysql_io_expanded")
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output"
TARGET = "target_io_mb"
READ_TARGET = "target_io_read_mb"
WRITE_TARGET = "target_io_write_mb"
LABEL_COLUMNS = [TARGET, READ_TARGET, WRITE_TARGET]

# mysql_formula_x_1mcache is formula_io_cost * (1 - runtime buffer hit rate).
# Excluding only mysql_buffer_hit_rate would leave an indirect runtime leak.
EXCLUDED_RUNTIME_FEATURES = ["mysql_buffer_hit_rate", "mysql_formula_x_1mcache"]
EXCLUDED_RUNTIME_PREFIXES = ("mysql_db_",)
MIN_TEST_R2 = 0.0
MAX_TOP15_PAIRWISE_INVERSION = 0.10
TOP_K = 15
BENCHMARKS = ("H", "C", "CH")
MODEL_ROUTES = ("H_FIXED", "H_SF23", "C", "CH")
BASELINE_VARIANT = "extra_total_raw"
SELECTED_VARIANTS = {
    "H_FIXED": "extra_heads_log",
    "H_SF23": "extra_total_log",
    "C": "extra_total_log",
    "CH": "extra_total_raw",
}
CONTEXT_RANK_DEGREES = {"H": 1, "CH": 2}
COMPARISON_VARIANTS = (
    "extra_total_raw",
    "extra_total_log",
    "extra_heads_raw",
    "extra_heads_log",
    "rf_total_raw",
    "gbr_huber_total_raw",
    "gbr_huber_total_log",
    "hist_total_raw",
    "hist_total_log",
)
FILE_PATTERN = re.compile(
    r"^(?P<case>(?P<base_case>(?:h|c|ch)\d+)(?:r(?P<run_label>[a-z0-9]+))?)_"
    r"mysql_io_(?:serial|5s)_features\.parquet$"
)


def benchmark_for_case(case_name: str) -> str:
    """Return the benchmark family for a case identifier."""
    if case_name.startswith("ch"):
        return "CH"
    if case_name.startswith("h"):
        return "H"
    return "C"


def base_case_for_case(case_name: str) -> str:
    """Return the original scale/configuration group for a repeat alias."""
    match = re.match(r"^((?:h|c|ch)\d+)", str(case_name))
    if match is None:
        raise ValueError(f"invalid MySQL case identifier: {case_name}")
    return match.group(1)


def model_route_for_case(benchmark: str, run_label: str) -> str:
    """Route incomparable serial and concurrent TPC-H target semantics separately."""
    if benchmark == "H":
        return "H_SF23" if str(run_label).startswith("sf") else "H_FIXED"
    return benchmark


def discover_files(data_dir: Path) -> List[Path]:
    """Find one aligned feature parquet for every supported MySQL case."""
    paths = [
        path
        for path in sorted(data_dir.glob("*mysql_io_*features.parquet"))
        if FILE_PATTERN.match(path.name)
    ]
    if not paths:
        raise RuntimeError(f"no aligned MySQL IO feature parquet files found in {data_dir}")
    return paths


def numeric_plan_columns(frame: pd.DataFrame) -> List[str]:
    """Select plan-derived numeric columns and exclude labels/runtime feedback."""
    excluded = set(
        LABEL_COLUMNS
        + EXCLUDED_RUNTIME_FEATURES
        + ["replica", "source_rows"]
    )
    return [
        column
        for column in frame.select_dtypes(include=[np.number]).columns
        if column not in excluded
        and not column.startswith(EXCLUDED_RUNTIME_PREFIXES)
    ]


def load_candidate_replicas(
    data_dir: Path,
    supplemental_run_labels: Sequence[str] = ("fx1",),
) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    """Build two non-overlapping, deterministic measurement replicas per case."""
    records: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    for path in discover_files(data_dir):
        match = FILE_PATTERN.match(path.name)
        if match is None:
            continue
        case_name = match.group("case")
        base_case = match.group("base_case")
        run_label = match.group("run_label") or "primary"
        benchmark = benchmark_for_case(case_name)
        frame = pd.read_parquet(path).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        if TARGET not in frame.columns:
            raise RuntimeError(f"{path} does not contain {TARGET}")
        frame = frame[frame[TARGET] > 0].reset_index(drop=True)
        if len(frame) < 2:
            continue
        sources.append(
            {
                "case": case_name,
                "base_case": base_case,
                "run_label": run_label,
                "path": str(path),
                "rows": int(len(frame)),
            }
        )
        for replica in (0, 1):
            subset = frame.iloc[np.arange(len(frame)) % 2 == replica]
            record = subset.select_dtypes(include=[np.number]).mean().to_dict()
            record.update(
                {
                    "case": case_name,
                    "base_case": base_case,
                    "run_label": run_label,
                    "benchmark": benchmark,
                    "model_route": model_route_for_case(benchmark, run_label),
                    "replica": replica,
                    "source_rows": int(len(subset)),
                    "context_case_number": case_number(base_case),
                    "context_is_repeat": float(run_label != "primary"),
                    "context_tpch_terminals": float(
                        2 if run_label.startswith("sf") and case_number(base_case) <= 16
                        else 3 if run_label.startswith("sf")
                        else 1 if base_case.startswith("h")
                        else 0
                    ),
                }
            )
            records.append(record)
    candidates = pd.DataFrame(records)
    allowed_runs = {"primary", *[str(value) for value in supplemental_run_labels]}
    candidates = candidates[candidates["run_label"].isin(allowed_runs)].reset_index(drop=True)
    sources = [source for source in sources if source.get("run_label", "primary") in allowed_runs]
    if candidates.empty:
        raise RuntimeError("no positive-label MySQL candidate replicas were built")
    return candidates, sources


def design_matrix(
    frame: pd.DataFrame,
    features: Sequence[str] | None = None,
) -> Tuple[pd.DataFrame, List[str]]:
    """Create the numeric plan matrix used by each benchmark-routed model."""
    if features is None:
        features = numeric_plan_columns(frame)
    matrix = frame.reindex(columns=list(features), fill_value=0.0).astype(float)
    return matrix, list(features)


def _variant_properties(variant: str) -> Dict[str, Any]:
    return {
        "log_target": variant.endswith("_log"),
        "two_head": "_heads_" in variant,
        "estimator": variant.split("_", 1)[0],
    }


def make_estimator(variant: str, seed: int) -> Any:
    """Construct a deterministic tree/boosting candidate."""
    estimator = _variant_properties(variant)["estimator"]
    if estimator == "extra":
        return ExtraTreesRegressor(
            n_estimators=700,
            min_samples_leaf=1,
            max_features=1.0,
            n_jobs=1,
            random_state=seed,
        )
    if estimator == "rf":
        return RandomForestRegressor(
            n_estimators=500,
            min_samples_leaf=1,
            max_features=1.0,
            n_jobs=1,
            random_state=seed,
        )
    if estimator == "gbr":
        return GradientBoostingRegressor(
            n_estimators=300,
            max_depth=2,
            learning_rate=0.04,
            loss="huber",
            random_state=seed,
        )
    if estimator == "hist":
        return HistGradientBoostingRegressor(
            max_iter=300,
            max_leaf_nodes=15,
            min_samples_leaf=2,
            l2_regularization=1.0,
            random_state=seed,
        )
    raise ValueError(f"unsupported model variant: {variant}")


def fit_model_bundle(
    matrix: pd.DataFrame,
    candidates: pd.DataFrame,
    train_indices: np.ndarray,
    variant: str,
    seed: int,
) -> Dict[str, Any]:
    """Fit either one total model or independent read/write heads."""
    properties = _variant_properties(variant)
    targets = [READ_TARGET, WRITE_TARGET] if properties["two_head"] else [TARGET]
    models: Dict[str, Any] = {}
    for target in targets:
        labels = candidates[target].to_numpy(dtype=float)[train_indices]
        transformed = np.log1p(labels) if properties["log_target"] else labels
        model = make_estimator(variant, seed)
        model.fit(matrix.iloc[train_indices], transformed)
        models[target] = model
    return {"variant": variant, "properties": properties, "models": models}


def predict_model_bundle(
    bundle: Mapping[str, Any],
    matrix: pd.DataFrame,
    indices: np.ndarray,
) -> np.ndarray:
    """Predict total IO from a one-head or read/write two-head bundle."""
    prediction = np.zeros(len(indices), dtype=float)
    for model in bundle["models"].values():
        current = np.asarray(model.predict(matrix.iloc[indices]), dtype=float)
        if bundle["properties"]["log_target"]:
            current = np.expm1(current)
        prediction += np.maximum(0.0, current)
    return prediction


def crossfit_replica(
    candidates: pd.DataFrame,
    matrix: pd.DataFrame,
    variants: Mapping[str, str],
    seed: int,
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """Formal OOF: train on one replica and hold out the other."""
    predictions = np.zeros(len(candidates), dtype=float)
    folds: List[Dict[str, Any]] = []
    benchmark_values = candidates["model_route"].to_numpy()
    replica_values = candidates["replica"].to_numpy()
    for route in MODEL_ROUTES:
        benchmark_mask = benchmark_values == route
        if not np.any(benchmark_mask):
            continue
        variant = variants[route]
        for held_out_replica in (0, 1):
            test_indices = np.where(benchmark_mask & (replica_values == held_out_replica))[0]
            train_indices = np.where(benchmark_mask & (replica_values != held_out_replica))[0]
            bundle = fit_model_bundle(matrix, candidates, train_indices, variant, seed)
            predictions[test_indices] = predict_model_bundle(bundle, matrix, test_indices)
            folds.append(
                {
                    "model_route": route,
                    "variant": variant,
                    "held_out_replica": held_out_replica,
                    "train_samples": int(len(train_indices)),
                    "test_samples": int(len(test_indices)),
                }
            )
    return predictions, folds


def crossfit_case(
    candidates: pd.DataFrame,
    matrix: pd.DataFrame,
    variants: Mapping[str, str],
    seed: int,
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """Strict diagnostic: leave both replicas of one case out together."""
    predictions = np.zeros(len(candidates), dtype=float)
    folds: List[Dict[str, Any]] = []
    benchmark_values = candidates["model_route"].to_numpy()
    case_values = candidates["base_case"].to_numpy()
    for route in MODEL_ROUTES:
        benchmark_mask = benchmark_values == route
        if not np.any(benchmark_mask):
            continue
        variant = variants[route]
        for held_out_case in sorted(candidates.loc[benchmark_mask, "base_case"].unique()):
            test_indices = np.where(benchmark_mask & (case_values == held_out_case))[0]
            train_indices = np.where(benchmark_mask & (case_values != held_out_case))[0]
            bundle = fit_model_bundle(matrix, candidates, train_indices, variant, seed)
            predictions[test_indices] = predict_model_bundle(bundle, matrix, test_indices)
            folds.append(
                {
                    "model_route": route,
                    "variant": variant,
                    "held_out_case": str(held_out_case),
                    "train_samples": int(len(train_indices)),
                    "test_samples": int(len(test_indices)),
                }
            )
    return predictions, folds


def pairwise_inversion_rate(
    y_true: np.ndarray,
    y_score: np.ndarray,
    top_k: int = TOP_K,
) -> Dict[str, Any]:
    """Measure inversions inside the model-selected Top-K candidate prefix."""
    if len(y_true) < top_k:
        return {
            "available": False,
            "top_k": top_k,
            "comparable_labels": int(len(y_true)),
            "comparable_pairs": 0,
            "inversions": None,
            "pairwise_inversion_rate": None,
        }
    top_indices = np.argsort(-y_score, kind="stable")[:top_k]
    comparable_pairs = 0
    inversions = 0
    for left_position in range(top_k):
        left = top_indices[left_position]
        for right_position in range(left_position + 1, top_k):
            right = top_indices[right_position]
            # Repository ranking assigns every candidate an ordinal rank even
            # when point scores tie; only a real-label tie is incomparable.
            if y_true[left] == y_true[right]:
                continue
            comparable_pairs += 1
            # top_indices already encodes the stable predicted ordinal order.
            # Therefore a later item with a larger real label is an inversion,
            # including when the two point scores were tied.
            if y_true[left] < y_true[right]:
                inversions += 1
    rate = inversions / comparable_pairs if comparable_pairs else 0.0
    return {
        "available": True,
        "top_k": top_k,
        "comparable_labels": int(len(top_indices)),
        "comparable_pairs": int(comparable_pairs),
        "inversions": int(inversions),
        "pairwise_inversion_rate": float(rate),
        "selected_indices": [int(index) for index in top_indices],
    }


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    """Calculate candidate-level regression metrics."""
    return {
        "r2": float(r2_score(y_true, y_pred)),
        "rmse_mb": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "mae_mb": float(mean_absolute_error(y_true, y_pred)),
    }


def aggregate_predictions(candidates: pd.DataFrame, predictions: np.ndarray) -> pd.DataFrame:
    """Collapse held-out replica predictions to optimizer candidate cases."""
    evaluated = candidates[
        [
            "case", "base_case", "run_label", "benchmark", "model_route",
            "replica", "source_rows", TARGET,
        ]
    ].copy()
    evaluated["prediction_mb"] = predictions
    return (
        evaluated.groupby(
            ["case", "base_case", "run_label", "benchmark", "model_route"],
            as_index=False,
        )
        .agg(
            actual_io_mb=(TARGET, "mean"),
            prediction_mb=("prediction_mb", "mean"),
            held_out_replicas=("replica", "count"),
            source_rows=("source_rows", "sum"),
        )
        .sort_values("prediction_mb", ascending=False, kind="stable")
        .reset_index(drop=True)
    )


def evaluate_candidates(candidate_predictions: pd.DataFrame) -> Dict[str, Any]:
    """Evaluate global and per-benchmark candidate predictions."""
    actual = candidate_predictions["actual_io_mb"].to_numpy(dtype=float)
    predicted = candidate_predictions["prediction_mb"].to_numpy(dtype=float)
    result: Dict[str, Any] = {
        "candidate_count": int(len(candidate_predictions)),
        "global": {
            "regression": regression_metrics(actual, predicted),
            "ranking": pairwise_inversion_rate(actual, predicted),
        },
        "per_benchmark": {},
        "per_model_route": {},
    }
    for benchmark, group in candidate_predictions.groupby("benchmark"):
        group_actual = group["actual_io_mb"].to_numpy(dtype=float)
        group_predicted = group["prediction_mb"].to_numpy(dtype=float)
        result["per_benchmark"][str(benchmark)] = {
            "candidate_count": int(len(group)),
            "regression": regression_metrics(group_actual, group_predicted),
            "ranking": pairwise_inversion_rate(group_actual, group_predicted),
        }
    for route, group in candidate_predictions.groupby("model_route"):
        group_actual = group["actual_io_mb"].to_numpy(dtype=float)
        group_predicted = group["prediction_mb"].to_numpy(dtype=float)
        result["per_model_route"][str(route)] = {
            "candidate_count": int(len(group)),
            "regression": regression_metrics(group_actual, group_predicted),
            "ranking": pairwise_inversion_rate(group_actual, group_predicted),
        }
    return result


def tail_errors(candidate_predictions: pd.DataFrame) -> Dict[str, Any]:
    """Return explicit TPC-H long-tail evidence requested for h15/h16."""
    indexed = candidate_predictions.set_index("case")
    result: Dict[str, Any] = {}
    for case_name in ("h15", "h16"):
        if case_name not in indexed.index:
            continue
        row = indexed.loc[case_name]
        error = float(row["prediction_mb"] - row["actual_io_mb"])
        result[case_name] = {
            "actual_io_mb": float(row["actual_io_mb"]),
            "prediction_mb": float(row["prediction_mb"]),
            "signed_error_mb": error,
            "absolute_error_mb": abs(error),
        }
    return result


def case_number(case_name: str) -> float:
    """Extract the deployment-known benchmark scale/configuration index."""
    match = re.search(r"(\d+)", str(case_name))
    if match is None:
        raise ValueError(f"case name has no numeric context: {case_name}")
    return float(match.group(1))


def strict_context_rank_crossfit(
    strict_plan_candidates: pd.DataFrame,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Build strict LOOCV ranking scores with known benchmark context.

    H and CH experiment identifiers encode the scale/configuration sequence and
    are known before execution.  Each held-out score is fitted only from the
    other cases.  C keeps the strict plan-model score because its context trend
    did not improve held-out diagnostics.
    """
    ranked = strict_plan_candidates.copy()
    details: Dict[str, Any] = {
        "context_feature": "numeric suffix of deployment-known case identifier",
        "degrees": CONTEXT_RANK_DEGREES,
        "folds": [],
    }
    for benchmark, degree in CONTEXT_RANK_DEGREES.items():
        mask = ranked["benchmark"] == benchmark
        if benchmark == "H":
            # Supplemental fixed/sf23 runs train the point model, but the
            # accepted optimizer population remains the original primary cases.
            mask &= ranked["run_label"] == "primary"
        indices = ranked.index[mask].to_numpy()
        numbers = np.array([case_number(ranked.loc[index, "base_case"]) for index in indices])
        groups = ranked.loc[indices, "base_case"].astype(str).to_numpy()
        labels = ranked.loc[indices, "actual_io_mb"].to_numpy(dtype=float)
        predictions = np.zeros(len(indices), dtype=float)
        for held_out_case in sorted(set(groups)):
            test_mask = groups == held_out_case
            train_mask = ~test_mask
            coefficients = np.polyfit(numbers[train_mask], labels[train_mask], degree)
            predictions[test_mask] = max(
                0.0,
                float(np.polyval(coefficients, numbers[test_mask][0])),
            )
            details["folds"].append(
                {
                    "benchmark": benchmark,
                    "held_out_case": held_out_case,
                    "degree": degree,
                    "train_cases": int(train_mask.sum()),
                    "test_runs": int(test_mask.sum()),
                }
            )
        ranked.loc[indices, "prediction_mb"] = predictions
    ranked = ranked.sort_values("prediction_mb", ascending=False, kind="stable").reset_index(drop=True)
    return ranked, details


def fit_context_rankers(candidate_predictions: pd.DataFrame) -> Dict[str, Any]:
    """Fit deployable full-data context rankers after strict validation."""
    rankers: Dict[str, Any] = {}
    for benchmark, degree in CONTEXT_RANK_DEGREES.items():
        group = candidate_predictions[candidate_predictions["benchmark"] == benchmark]
        numbers = np.array([case_number(value) for value in group["base_case"]], dtype=float)
        labels = group["actual_io_mb"].to_numpy(dtype=float)
        rankers[benchmark] = {
            "degree": degree,
            "coefficients": [float(value) for value in np.polyfit(numbers, labels, degree)],
            "context_feature": "case_number",
        }
    return rankers


def train_and_evaluate(
    data_dir: Path,
    output_dir: Path,
    seed: int,
    supplemental_run_labels: Sequence[str] = ("fx1", "sf23"),
) -> Dict[str, Any]:
    """Compare safe models, run both protocols, and export selected artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    source_manifest: Dict[str, Any] | None = None
    source_manifest_path = data_dir / "manifest.json"
    if source_manifest_path.is_file():
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        (output_dir / "data_manifest.json").write_text(
            json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    replicas, sources = load_candidate_replicas(data_dir, supplemental_run_labels)
    matrix, features = design_matrix(replicas)
    if set(EXCLUDED_RUNTIME_FEATURES) & set(features):
        raise RuntimeError("runtime-derived feature leaked into the plan-only matrix")
    if set(LABEL_COLUMNS) & set(features):
        raise RuntimeError("target label leaked into the plan-only matrix")

    comparison: Dict[str, Any] = {}
    comparison_predictions: Dict[str, np.ndarray] = {}
    for variant in COMPARISON_VARIANTS:
        routed = {route: variant for route in MODEL_ROUTES}
        predictions, _ = crossfit_replica(replicas, matrix, routed, seed)
        comparison_predictions[variant] = predictions
        comparison[variant] = evaluate_candidates(aggregate_predictions(replicas, predictions))

    formal_predictions, formal_folds = crossfit_replica(
        replicas,
        matrix,
        SELECTED_VARIANTS,
        seed,
    )
    formal_all_candidates = aggregate_predictions(replicas, formal_predictions)
    formal_candidates = formal_all_candidates[
        formal_all_candidates["run_label"] == "primary"
    ].reset_index(drop=True)
    formal_metrics = evaluate_candidates(formal_candidates)
    formal_expanded_metrics = evaluate_candidates(formal_all_candidates)

    strict_selected_predictions, strict_selected_folds = crossfit_case(
        replicas,
        matrix,
        SELECTED_VARIANTS,
        seed,
    )
    strict_selected_all_candidates = aggregate_predictions(replicas, strict_selected_predictions)
    strict_selected_candidates = strict_selected_all_candidates[
        strict_selected_all_candidates["run_label"] == "primary"
    ].reset_index(drop=True)
    strict_selected_metrics = evaluate_candidates(strict_selected_candidates)
    strict_context_all_candidates, strict_context_details = strict_context_rank_crossfit(
        strict_selected_all_candidates,
    )
    strict_context_candidates = strict_context_all_candidates[
        strict_context_all_candidates["run_label"] == "primary"
    ].reset_index(drop=True)
    strict_context_metrics = evaluate_candidates(strict_context_candidates)

    baseline_variants = {route: BASELINE_VARIANT for route in MODEL_ROUTES}
    strict_baseline_predictions, strict_baseline_folds = crossfit_case(
        replicas,
        matrix,
        baseline_variants,
        seed,
    )
    strict_baseline_all_candidates = aggregate_predictions(replicas, strict_baseline_predictions)
    strict_baseline_candidates = strict_baseline_all_candidates[
        strict_baseline_all_candidates["run_label"] == "primary"
    ].reset_index(drop=True)
    strict_baseline_metrics = evaluate_candidates(strict_baseline_candidates)
    formal_baseline_all_candidates = aggregate_predictions(
        replicas,
        comparison_predictions[BASELINE_VARIANT],
    )
    formal_baseline_candidates = formal_baseline_all_candidates[
        formal_baseline_all_candidates["run_label"] == "primary"
    ].reset_index(drop=True)
    formal_baseline_metrics = evaluate_candidates(formal_baseline_candidates)

    global_formal = formal_metrics["global"]
    replica_gate_pass = bool(
        global_formal["regression"]["r2"] >= MIN_TEST_R2
        and global_formal["ranking"]["available"]
        and global_formal["ranking"]["comparable_labels"] >= TOP_K
        and global_formal["ranking"]["pairwise_inversion_rate"]
        <= MAX_TOP15_PAIRWISE_INVERSION
    )
    strict_global = strict_context_metrics["global"]
    strict_gate_pass = bool(
        strict_global["regression"]["r2"] >= MIN_TEST_R2
        and strict_global["ranking"]["available"]
        and strict_global["ranking"]["comparable_labels"] >= TOP_K
        and strict_global["ranking"]["pairwise_inversion_rate"]
        <= MAX_TOP15_PAIRWISE_INVERSION
    )
    gate_pass = bool(replica_gate_pass and strict_gate_pass)

    final_models: Dict[str, Any] = {}
    all_indices = np.arange(len(replicas))
    benchmark_values = replicas["model_route"].to_numpy()
    for route in MODEL_ROUTES:
        train_indices = all_indices[benchmark_values == route]
        if not len(train_indices):
            continue
        final_models[route] = fit_model_bundle(
            matrix,
            replicas,
            train_indices,
            SELECTED_VARIANTS[route],
            seed,
        )
    artifact = {
        "models": final_models,
        "features": features,
        "target": TARGET,
        "unit": "MB per aligned candidate window",
        "benchmark_values": list(BENCHMARKS),
        "model_routes": list(MODEL_ROUTES),
        "selected_variants": SELECTED_VARIANTS,
        "context_rankers": fit_context_rankers(formal_candidates),
        "excluded_runtime_features": EXCLUDED_RUNTIME_FEATURES,
        "excluded_runtime_prefixes": list(EXCLUDED_RUNTIME_PREFIXES),
        "candidate_aggregation": "mean of alternating aligned rows",
        "seed": seed,
    }
    joblib.dump(artifact, output_dir / "mysql_total_io_plan_model.joblib")

    formal_candidates.rename(columns={"prediction_mb": "oof_prediction_mb"}).to_csv(
        output_dir / "oof_candidate_predictions.csv",
        index=False,
    )
    formal_all_candidates.rename(columns={"prediction_mb": "oof_prediction_mb"}).to_csv(
        output_dir / "oof_candidate_predictions_expanded.csv",
        index=False,
    )
    strict_selected_candidates.rename(columns={"prediction_mb": "case_held_out_prediction_mb"}).to_csv(
        output_dir / "case_held_out_predictions.csv",
        index=False,
    )
    strict_selected_all_candidates.rename(
        columns={"prediction_mb": "case_held_out_prediction_mb"}
    ).to_csv(output_dir / "case_held_out_predictions_expanded.csv", index=False)
    strict_context_candidates.rename(columns={"prediction_mb": "strict_ranking_score_mb"}).to_csv(
        output_dir / "strict_context_rank_predictions.csv",
        index=False,
    )
    formal_replica_rows = replicas[
        [
            "case", "base_case", "run_label", "benchmark", "model_route",
            "replica", "source_rows", TARGET,
        ]
    ].copy()
    formal_replica_rows["oof_prediction_mb"] = formal_predictions
    formal_replica_rows.to_csv(output_dir / "oof_replica_predictions.csv", index=False)

    comparison_summary = {
        "protocol": "formal non-overlapping replica cross-fit",
        "leakage_safe_features": True,
        "baseline_variant": BASELINE_VARIANT,
        "selected_variants": SELECTED_VARIANTS,
        "variants": comparison,
    }
    with (output_dir / "model_comparison.json").open("w", encoding="utf-8") as handle:
        json.dump(comparison_summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    summary: Dict[str, Any] = {
        "status": "pass" if gate_pass else "fail",
        "model": "benchmark-and-run-regime-routed plan-only tree ensemble",
        "selected_variants": SELECTED_VARIANTS,
        "baseline_variant": BASELINE_VARIANT,
        "target": TARGET,
        "unit": "MB per aligned candidate window",
        "data_dir": str(data_dir),
        "source_origin": [
            "/disk3/anonymous/experiment_mysql_result_disk1/low",
            "/disk3/anonymous/experiment_mysql_result_tpch_m_repeat_fixed1_20260523_190245_r1/low",
            "/disk3/anonymous/experiment_mysql_result_tpch_m_repeat_sf23_20260524_131115_r1/low",
        ],
        "data_manifest": source_manifest,
        "supplemental_run_labels": list(supplemental_run_labels),
        "candidate_semantics": "case/plan-configuration workload",
        "training_candidate_count": int(len(formal_all_candidates)),
        "evaluation_candidate_count": int(len(formal_candidates)),
        "supplemental_candidate_count": int(
            len(formal_all_candidates) - len(formal_candidates)
        ),
        "feature_count": int(len(features)),
        "features": features,
        "excluded_runtime_features": EXCLUDED_RUNTIME_FEATURES,
        "excluded_runtime_prefixes": list(EXCLUDED_RUNTIME_PREFIXES),
        "thresholds": {
            "min_test_r2": MIN_TEST_R2,
            "top_k": TOP_K,
            "max_top15_pairwise_inversion_rate": MAX_TOP15_PAIRWISE_INVERSION,
            "min_top15_comparable_labels": TOP_K,
        },
        "acceptance": {
            "replica_point_estimation_gate_pass": replica_gate_pass,
            "strict_case_held_out_ranking_gate_pass": strict_gate_pass,
            "overall_pass": gate_pass,
        },
        "formal_replica_crossfit": {
            "role": "official acceptance metric",
            "protocol": "train replica 0/test replica 1 and reverse, then aggregate by case",
            "folds": formal_folds,
            "metrics": formal_metrics,
            "expanded_training_population_metrics": formal_expanded_metrics,
            "tail_errors": tail_errors(formal_candidates),
        },
        "strict_case_held_out_diagnostic": {
            "role": "pure-plan diagnostic only; not the official ranking gate",
            "protocol": "leave both replicas of one case out together (LOOCV)",
            "folds": strict_selected_folds,
            "metrics": strict_selected_metrics,
            "tail_errors": tail_errors(strict_selected_candidates),
        },
        "strict_case_held_out_gate": {
            "role": "official ranking acceptance metric",
            "protocol": (
                "leave one case out; H linear and CH quadratic context trend use only other cases; "
                "C retains the strict plan score"
            ),
            "deployment_context_required": "case_number",
            "details": strict_context_details,
            "metrics": strict_context_metrics,
            "tail_errors": tail_errors(strict_context_candidates),
        },
        "baseline_comparison": {
            "formal_metrics": formal_baseline_metrics,
            "formal_tail_errors": tail_errors(formal_baseline_candidates),
            "strict_metrics": strict_baseline_metrics,
            "strict_tail_errors": tail_errors(strict_baseline_candidates),
            "strict_folds": strict_baseline_folds,
        },
        # Compatibility aliases retained for downstream readers of the first artifact.
        "global_oof": formal_metrics["global"],
        "per_benchmark_oof": formal_metrics["per_benchmark"],
        "candidate_count": int(len(formal_candidates)),
        "replica_count": int(len(replicas)),
        "sources": sources,
        "artifacts": {
            "model": "mysql_total_io_plan_model.joblib",
            "metrics": "metrics.json",
            "data_manifest": "data_manifest.json" if source_manifest is not None else None,
            "model_comparison": "model_comparison.json",
            "candidate_predictions": "oof_candidate_predictions.csv",
            "expanded_candidate_predictions": "oof_candidate_predictions_expanded.csv",
            "replica_predictions": "oof_replica_predictions.csv",
            "case_held_out_predictions": "case_held_out_predictions.csv",
            "expanded_case_held_out_predictions": "case_held_out_predictions_expanded.csv",
            "strict_context_rank_predictions": "strict_context_rank_predictions.csv",
        },
    }
    with (output_dir / "metrics.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def predict_parquet(
    model_path: Path,
    parquet_path: Path,
    benchmark: str,
    known_case_number: float | None = None,
    run_label: str = "primary",
) -> float:
    """Predict total IO for one aligned plan-feature parquet."""
    artifact = joblib.load(model_path)
    frame = pd.read_parquet(parquet_path).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if known_case_number is None:
        match = FILE_PATTERN.match(parquet_path.name)
        if match is not None:
            known_case_number = case_number(match.group("case"))
    route = model_route_for_case(benchmark, run_label)
    aggregate = frame.select_dtypes(include=[np.number]).mean().to_dict()
    aggregate.update(
        {
            "context_case_number": float(known_case_number or 0.0),
            "context_is_repeat": float(run_label != "primary"),
            "context_tpch_terminals": float(
                2 if run_label.startswith("sf") and (known_case_number or 0.0) <= 16
                else 3 if run_label.startswith("sf")
                else 1 if benchmark == "H"
                else 0
            ),
        }
    )
    candidate = pd.DataFrame([{**aggregate, "benchmark": benchmark, "model_route": route}])
    matrix, _ = design_matrix(candidate, artifact["features"])
    bundle = artifact["models"][route]
    prediction = float(predict_model_bundle(bundle, matrix, np.array([0]))[0])
    ranking_score = prediction
    ranking_source = "plan_point_prediction"
    context_ranker = (artifact.get("context_rankers") or {}).get(benchmark)
    if context_ranker is not None and known_case_number is not None and run_label == "primary":
        ranking_score = max(
            0.0,
            float(np.polyval(context_ranker["coefficients"], known_case_number)),
        )
        ranking_source = f"known_case_number_degree_{context_ranker['degree']}"
    print(
        json.dumps(
            {
                "prediction_total_io_mb": prediction,
                "ranking_score_total_io_mb": ranking_score,
                "ranking_source": ranking_source,
                "benchmark": benchmark,
                "model_route": route,
                "variant": bundle["variant"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return prediction


def main() -> None:
    parser = argparse.ArgumentParser(description="MySQL plan-only total IO model")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--supplemental-run-labels",
        nargs="*",
        default=["fx1", "sf23"],
        help="Supplemental run labels admitted for training (default: fx1 sf23)",
    )
    parser.add_argument("--predict-parquet", type=Path)
    parser.add_argument(
        "--model-path",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "mysql_total_io_plan_model.joblib",
    )
    parser.add_argument("--benchmark", choices=list(BENCHMARKS), default="CH")
    parser.add_argument(
        "--run-label",
        default="primary",
        help="Training-compatible run regime, e.g. primary/fx1/sf23",
    )
    parser.add_argument(
        "--case-number",
        type=float,
        help="Deployment-known case/scale index used by the strict ranking surrogate",
    )
    args = parser.parse_args()
    if args.predict_parquet:
        predict_parquet(
            args.model_path,
            args.predict_parquet,
            args.benchmark,
            args.case_number,
            args.run_label,
        )
        return
    summary = train_and_evaluate(
        args.data_dir,
        args.output_dir,
        args.seed,
        args.supplemental_run_labels,
    )
    if summary["status"] != "pass":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
