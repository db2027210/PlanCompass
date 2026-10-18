#!/usr/bin/env python3
"""Train and evaluate MySQL plan-tree memory models.

The total working-set prediction is deliberately decomposed as::

    predicted_total_mb = observed_base_mb + predicted_plan_dynamic_mb

``causal_base_mb`` uses only memory observations available at the candidate
boundary.  The learned part uses MySQL EXPLAIN-plan features and online context.
This avoids asking a plan tree to reconstruct the static InnoDB buffer pool
while keeping the deployed prediction in the same MB unit as the monitor.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold


DEFAULT_BASE_DIR = Path("/disk3/anonymous/experiment_mysql_result_disk1/low")
DEFAULT_FEATURE_DIR = Path("/home/anonymous/program/features_cache_M1")
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "artifacts"
R2_THRESHOLD = 0.0
TOP15_INVERSION_THRESHOLD = 0.10
BASELINE_SHRINKAGE_BY_BENCHMARK = {"H": 0.0, "C": 0.01, "CH": 0.05}
OPTIMIZED_SHRINKAGE_BY_BENCHMARK = {"H": 0.0, "C": 0.10, "CH": 0.10}
DRIFT_LOOKAHEAD_STEPS = 4
DRIFT_HISTORY_DIFFERENCES = 6

LABEL_COLUMNS = {
    "target_io_read_mb",
    "target_io_write_mb",
    "target_io_mb",
    "target_cpu",
}


def _plan_columns(frame: pd.DataFrame) -> List[str]:
    """Return plan-derived columns, excluding labels and runtime leakage."""
    return [
        column
        for column in frame.columns
        if column not in LABEL_COLUMNS
        and column != "mysql_buffer_hit_rate"
        and not column.startswith("mysql_db_")
        and pd.api.types.is_numeric_dtype(frame[column])
    ]


def _aggregate_candidate(
    feature_frame: pd.DataFrame,
    *,
    benchmark: str,
    case_name: str,
    block_index: int,
    single_point_base_mb: float,
    causal_base_mb: float,
    positive_drift_mb_per_window: float,
    candidate_progress: float,
    target_total_mb: float,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "candidate_id": f"{benchmark}:{case_name}:block_{block_index:03d}",
        "benchmark": benchmark,
        "case": case_name,
        "group": f"{benchmark}:{case_name}",
        "single_point_base_memory_mb": float(single_point_base_mb),
        "observed_base_memory_mb": float(causal_base_mb),
        "context_last_memory_mb": float(single_point_base_mb),
        "context_positive_drift_mb_per_window": float(positive_drift_mb_per_window),
        "context_block_index": float(block_index),
        "context_candidate_progress": float(candidate_progress),
        "target_memory_mb": float(target_total_mb),
    }
    for column in _plan_columns(feature_frame):
        values = pd.to_numeric(feature_frame[column], errors="coerce").fillna(0.0)
        row[f"{column}__mean"] = float(values.mean())
        row[f"{column}__max"] = float(values.max())
    return row


def _valid_resampled_memory(case_dir: Path, window_s: int) -> pd.DataFrame:
    memory_path = case_dir / "metrics/mysql_mysqld_working_set_mb.csv"
    cpu_path = case_dir / "metrics/container_cpu_time.csv"
    memory = pd.read_csv(memory_path)
    cpu = pd.read_csv(cpu_path)
    memory["ts"] = pd.to_datetime(memory["timestamp"], errors="coerce")
    cpu["ts"] = pd.to_datetime(cpu["timestamp"], errors="coerce")
    memory["mysqld_working_set_mb"] = pd.to_numeric(
        memory["mysqld_working_set_mb"], errors="coerce"
    )
    memory = memory.dropna(subset=["ts", "mysqld_working_set_mb"])
    cpu = cpu.dropna(subset=["ts"])
    resampled = (
        memory.set_index("ts")["mysqld_working_set_mb"]
        .resample(f"{window_s}s")
        .agg(["first", "max"])
        .reset_index()
    )
    experiment_start = cpu["ts"].min()
    experiment_end = cpu["ts"].max()
    window_end = resampled["ts"] + pd.Timedelta(seconds=window_s)
    valid_seconds = (
        np.minimum(window_end, experiment_end)
        - np.maximum(resampled["ts"], experiment_start)
    ).dt.total_seconds()
    resampled = resampled[valid_seconds >= window_s * 0.95].reset_index(drop=True)
    first_values = resampled["first"].to_numpy(dtype=float)
    positive_drift = np.zeros(len(resampled), dtype=float)
    causal_base = first_values.copy()
    for index in range(len(resampled)):
        # The current value is observed at the candidate boundary.  Every
        # difference ends at or before that boundary, so future labels cannot
        # enter this drift estimate.
        history = first_values[: index + 1]
        history = history[np.isfinite(history)]
        if len(history) >= 2:
            differences = np.diff(history)[-DRIFT_HISTORY_DIFFERENCES:]
            drift = max(float(np.median(differences)), 0.0)
            positive_drift[index] = drift
            causal_base[index] = first_values[index] + DRIFT_LOOKAHEAD_STEPS * drift
    resampled["positive_drift_mb_per_window"] = positive_drift
    resampled["causal_base_memory_mb"] = causal_base
    return resampled


def _concurrent_candidates(
    base_dir: Path,
    feature_dir: Path,
    benchmark: str,
    cases: Iterable[str],
    *,
    block_rows: int,
    window_s: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    candidates: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for case_name in cases:
        case_dir = base_dir / case_name
        feature_path = feature_dir / f"{case_name}_mysql_io_{window_s}s_features.parquet"
        required = [
            feature_path,
            case_dir / "metrics/mysql_mysqld_working_set_mb.csv",
            case_dir / "metrics/container_cpu_time.csv",
        ]
        if not all(path.exists() for path in required):
            skipped.append({"benchmark": benchmark, "case": case_name, "reason": "missing_input"})
            continue
        features = pd.read_parquet(feature_path).reset_index(drop=True)
        memory = _valid_resampled_memory(case_dir, window_s)
        if len(features) != len(memory):
            skipped.append(
                {
                    "benchmark": benchmark,
                    "case": case_name,
                    "reason": "window_count_mismatch",
                    "feature_rows": len(features),
                    "memory_rows": len(memory),
                }
            )
            continue
        starts = list(range(0, len(features), block_rows))
        for block_index, start in enumerate(starts):
            feature_block = features.iloc[start : start + block_rows]
            memory_block = memory.iloc[start : start + block_rows]
            if len(feature_block) < max(3, block_rows // 2):
                continue
            if "active_sql_count" in feature_block and feature_block["active_sql_count"].max() <= 0:
                continue
            candidates.append(
                _aggregate_candidate(
                    feature_block,
                    benchmark=benchmark,
                    case_name=case_name,
                    block_index=block_index,
                    single_point_base_mb=float(memory_block["first"].iloc[0]),
                    causal_base_mb=float(memory_block["causal_base_memory_mb"].iloc[0]),
                    positive_drift_mb_per_window=float(
                        memory_block["positive_drift_mb_per_window"].iloc[0]
                    ),
                    candidate_progress=block_index / max(len(starts) - 1, 1),
                    target_total_mb=float(memory_block["max"].max()),
                )
            )
    return candidates, skipped


def _serial_candidates(
    base_dir: Path,
    feature_dir: Path,
    cases: Iterable[str],
    *,
    block_rows: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build H candidates using ordinal alignment.

    Several archived H SQL logs have copied absolute timestamps that differ
    from the metric timestamps.  Both streams preserve order, so the metric
    stream is split into the same number of ordinal blocks as the plan stream.
    The alignment mode is recorded in the report and H remains a diagnostic;
    C/CH use exact timestamp alignment.
    """
    candidates: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for case_name in cases:
        case_dir = base_dir / case_name
        feature_path = feature_dir / f"{case_name}_mysql_io_serial_features.parquet"
        memory_path = case_dir / "metrics/mysql_mysqld_working_set_mb.csv"
        if not feature_path.exists() or not memory_path.exists():
            skipped.append({"benchmark": "H", "case": case_name, "reason": "missing_input"})
            continue
        features = pd.read_parquet(feature_path).reset_index(drop=True)
        memory = pd.read_csv(memory_path)
        values = pd.to_numeric(memory["mysqld_working_set_mb"], errors="coerce").dropna().to_numpy()
        starts = list(range(0, len(features), block_rows))
        metric_blocks = np.array_split(values, len(starts))
        for block_index, (start, metric_values) in enumerate(zip(starts, metric_blocks)):
            feature_block = features.iloc[start : start + block_rows]
            if len(feature_block) < max(3, block_rows // 2) or len(metric_values) < 2:
                continue
            candidates.append(
                _aggregate_candidate(
                    feature_block,
                    benchmark="H",
                    case_name=case_name,
                    block_index=block_index,
                    single_point_base_mb=float(metric_values[0]),
                    causal_base_mb=float(metric_values[0]),
                    positive_drift_mb_per_window=0.0,
                    candidate_progress=block_index / max(len(starts) - 1, 1),
                    target_total_mb=float(np.nanmax(metric_values)),
                )
            )
    return candidates, skipped


def build_candidates(
    base_dir: Path,
    feature_dir: Path,
    *,
    block_rows: int,
    window_s: int,
) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    all_candidates: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for benchmark, cases in (
        ("C", (f"c{i}" for i in range(11, 21))),
        ("CH", (f"ch{i}" for i in range(21, 41))),
    ):
        rows, missing = _concurrent_candidates(
            base_dir,
            feature_dir,
            benchmark,
            cases,
            block_rows=block_rows,
            window_s=window_s,
        )
        all_candidates.extend(rows)
        skipped.extend(missing)
    rows, missing = _serial_candidates(
        base_dir,
        feature_dir,
        (f"h{i}" for i in range(11, 21)),
        block_rows=block_rows,
    )
    all_candidates.extend(rows)
    skipped.extend(missing)
    frame = pd.DataFrame(all_candidates).fillna(0.0)
    if frame.empty:
        raise RuntimeError("No usable MySQL memory candidates were found")
    return frame, skipped


def _feature_matrix(
    frame: pd.DataFrame,
    *,
    include_context: bool,
) -> Tuple[np.ndarray, List[str]]:
    excluded = {
        "candidate_id",
        "benchmark",
        "case",
        "group",
        "single_point_base_memory_mb",
        "observed_base_memory_mb",
        "target_memory_mb",
        "fold",
        "actual_dynamic_memory_mb",
        "baseline_oof_prediction_memory_mb",
        "oof_prediction_dynamic_memory_mb",
        "oof_dynamic_shrinkage",
        "oof_prediction_memory_mb",
    }
    augmented = frame.copy()
    for benchmark in ("H", "C", "CH"):
        augmented[f"benchmark__{benchmark}"] = (augmented["benchmark"] == benchmark).astype(float)
    columns = sorted(
        column
        for column in augmented.columns
        if column not in excluded
        and (include_context or not column.startswith("context_"))
    )
    matrix = augmented.reindex(columns=columns, fill_value=0.0).to_numpy(dtype=float)
    return np.nan_to_num(matrix, nan=0.0, posinf=0.0, neginf=0.0), columns


def _new_model(seed: int) -> ExtraTreesRegressor:
    return ExtraTreesRegressor(
        n_estimators=500,
        min_samples_leaf=2,
        max_features=0.8,
        n_jobs=-1,
        random_state=seed,
    )


def _pairwise_top15(actual: np.ndarray, predicted: np.ndarray) -> Dict[str, Any]:
    top_k = min(15, len(actual))
    # Production first selects candidates by the predicted score.  Selecting
    # this prefix by the true label would leak the answer into the gate.
    indices = np.argsort(-predicted, kind="stable")[:top_k]
    inversions = 0
    comparable_pairs = 0
    for left in range(top_k):
        for right in range(left + 1, top_k):
            actual_delta = actual[indices[left]] - actual[indices[right]]
            predicted_delta = predicted[indices[left]] - predicted[indices[right]]
            if actual_delta == 0:
                continue
            comparable_pairs += 1
            if actual_delta * predicted_delta < 0:
                inversions += 1
    rate = inversions / comparable_pairs if comparable_pairs else 0.0
    return {
        "top_k": top_k,
        "comparable_labels": top_k,
        "comparable_pairs": comparable_pairs,
        "inversions": inversions,
        "inversion_rate": rate,
        "indices": indices.tolist(),
    }


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> Dict[str, Any]:
    ranking = _pairwise_top15(actual, predicted)
    absolute_error = np.abs(actual - predicted)
    result: Dict[str, Any] = {
        "samples": len(actual),
        "r2": float(r2_score(actual, predicted)),
        "rmse_mb": float(math.sqrt(mean_squared_error(actual, predicted))),
        "mae_mb": float(mean_absolute_error(actual, predicted)),
        "median_absolute_error_mb": float(np.median(absolute_error)),
        "p95_absolute_error_mb": float(np.percentile(absolute_error, 95)),
        "max_absolute_error_mb": float(np.max(absolute_error)),
        "top15": ranking,
    }
    result["quality_gate_passed"] = bool(
        result["r2"] >= R2_THRESHOLD
        and ranking["comparable_labels"] == 15
        and ranking["inversion_rate"] <= TOP15_INVERSION_THRESHOLD
    )
    return result


def _grouped_oof(
    frame: pd.DataFrame,
    matrix: np.ndarray,
    *,
    base_column: str,
    shrinkage_by_benchmark: Dict[str, float],
    seed: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return total/dynamic/shrinkage/fold predictions for grouped OOF."""
    actual = frame["target_memory_mb"].to_numpy(dtype=float)
    observed_base = frame[base_column].to_numpy(dtype=float)
    dynamic_target = np.maximum(actual - observed_base, 0.0)
    groups = frame["group"].astype(str).to_numpy()
    benchmarks = frame["benchmark"].astype(str).to_numpy()
    folds = min(5, len(np.unique(groups)))
    if folds < 2:
        raise RuntimeError("Grouped OOF evaluation needs at least two cases")

    oof_dynamic = np.zeros(len(frame), dtype=float)
    oof_shrinkage = np.zeros(len(frame), dtype=float)
    fold_id = np.full(len(frame), -1, dtype=int)
    splitter = GroupKFold(n_splits=folds)
    for current_fold, (train_indices, test_indices) in enumerate(
        splitter.split(matrix, dynamic_target, groups)
    ):
        model = _new_model(seed)
        model.fit(matrix[train_indices], dynamic_target[train_indices])
        oof_dynamic[test_indices] = np.maximum(model.predict(matrix[test_indices]), 0.0)
        oof_shrinkage[test_indices] = np.asarray(
            [shrinkage_by_benchmark[name] for name in benchmarks[test_indices]],
            dtype=float,
        )
        fold_id[test_indices] = current_fold
    oof_total = observed_base + oof_shrinkage * oof_dynamic
    return oof_total, oof_dynamic, oof_shrinkage, fold_id


def train_evaluate(
    candidates: pd.DataFrame,
    *,
    seed: int,
    output_dir: Path,
    skipped: Sequence[Dict[str, Any]],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = candidates.copy()
    exact_mask = candidates["benchmark"].to_numpy() != "H"
    formal = candidates.loc[exact_mask].reset_index(drop=True)
    diagnostic_h = candidates.loc[~exact_mask].reset_index(drop=True)

    baseline_matrix, baseline_columns = _feature_matrix(formal, include_context=False)
    baseline_total, _, _, _ = _grouped_oof(
        formal,
        baseline_matrix,
        base_column="single_point_base_memory_mb",
        shrinkage_by_benchmark=BASELINE_SHRINKAGE_BY_BENCHMARK,
        seed=seed,
    )
    optimized_matrix, feature_columns = _feature_matrix(formal, include_context=True)
    optimized_total, optimized_dynamic, optimized_shrinkage, fold_id = _grouped_oof(
        formal,
        optimized_matrix,
        base_column="observed_base_memory_mb",
        shrinkage_by_benchmark=OPTIMIZED_SHRINKAGE_BY_BENCHMARK,
        seed=seed,
    )

    # H remains an ordinal-alignment diagnostic and is evaluated independently,
    # so its noisy archive alignment cannot affect the formal C+CH model.
    h_matrix, _ = _feature_matrix(diagnostic_h, include_context=True)
    h_total, h_dynamic, h_shrinkage, h_fold = _grouped_oof(
        diagnostic_h,
        h_matrix,
        base_column="observed_base_memory_mb",
        shrinkage_by_benchmark=OPTIMIZED_SHRINKAGE_BY_BENCHMARK,
        seed=seed,
    )

    formal["fold"] = fold_id
    formal["actual_dynamic_memory_mb"] = np.maximum(
        formal["target_memory_mb"].to_numpy(dtype=float)
        - formal["observed_base_memory_mb"].to_numpy(dtype=float),
        0.0,
    )
    formal["baseline_oof_prediction_memory_mb"] = baseline_total
    formal["oof_prediction_dynamic_memory_mb"] = optimized_dynamic
    formal["oof_dynamic_shrinkage"] = optimized_shrinkage
    formal["oof_prediction_memory_mb"] = optimized_total
    diagnostic_h["fold"] = h_fold
    diagnostic_h["actual_dynamic_memory_mb"] = np.maximum(
        diagnostic_h["target_memory_mb"].to_numpy(dtype=float)
        - diagnostic_h["observed_base_memory_mb"].to_numpy(dtype=float),
        0.0,
    )
    diagnostic_h["baseline_oof_prediction_memory_mb"] = h_total
    diagnostic_h["oof_prediction_dynamic_memory_mb"] = h_dynamic
    diagnostic_h["oof_dynamic_shrinkage"] = h_shrinkage
    diagnostic_h["oof_prediction_memory_mb"] = h_total
    candidates = pd.concat([formal, diagnostic_h], ignore_index=True)

    formal_actual = formal["target_memory_mb"].to_numpy(dtype=float)
    overall = _metrics(formal_actual, optimized_total)
    baseline_overall = _metrics(formal_actual, baseline_total)
    all_actual = candidates["target_memory_mb"].to_numpy(dtype=float)
    all_prediction = candidates["oof_prediction_memory_mb"].to_numpy(dtype=float)
    all_candidate_diagnostic = _metrics(all_actual, all_prediction)
    by_benchmark: Dict[str, Any] = {}
    for benchmark in ("H", "C", "CH"):
        mask = candidates["benchmark"].to_numpy() == benchmark
        by_benchmark[benchmark] = _metrics(all_actual[mask], all_prediction[mask])
        by_benchmark[benchmark]["alignment"] = (
            "ordinal_diagnostic" if benchmark == "H" else "exact_timestamp"
        )

    overall_top_indices = overall["top15"].pop("indices")
    overall["top15"]["candidates"] = [
        {
            "candidate_id": str(formal.iloc[index]["candidate_id"]),
            "actual_mb": float(formal_actual[index]),
            "predicted_mb": float(optimized_total[index]),
        }
        for index in overall_top_indices
    ]
    baseline_overall["top15"].pop("indices", None)
    for benchmark in ("H", "C", "CH"):
        by_benchmark[benchmark]["top15"].pop("indices", None)

    signed_error = optimized_total - formal_actual
    largest_error_indices = np.argsort(-np.abs(signed_error), kind="stable")[:15]
    error_diagnostics = {
        "largest_absolute_errors": [
            {
                "candidate_id": str(formal.iloc[index]["candidate_id"]),
                "benchmark": str(formal.iloc[index]["benchmark"]),
                "actual_mb": float(formal_actual[index]),
                "predicted_mb": float(optimized_total[index]),
                "signed_error_mb": float(signed_error[index]),
            }
            for index in largest_error_indices
        ],
        "underprediction_count": int(np.sum(signed_error < 0)),
        "overprediction_count": int(np.sum(signed_error > 0)),
        "largest_underprediction_mb": float(max(-np.min(signed_error), 0.0)),
        "largest_overprediction_mb": float(max(np.max(signed_error), 0.0)),
    }

    optimized_dynamic_target = np.maximum(
        formal_actual - formal["observed_base_memory_mb"].to_numpy(dtype=float), 0.0
    )
    final_model = _new_model(seed)
    final_model.fit(optimized_matrix, optimized_dynamic_target)
    final_shrinkage = dict(OPTIMIZED_SHRINKAGE_BY_BENCHMARK)
    artifact = {
        "model": final_model,
        "feature_columns": feature_columns,
        "prediction_contract": {
            "learned_target": "dynamic_memory_mb=max(total_memory_mb-causal_base_memory_mb,0)",
            "total_prediction": (
                "observed_base_memory_mb + benchmark_shrinkage * "
                "max(model_dynamic_prediction, 0)"
            ),
            "causal_base": (
                "last_memory_mb + 4 * max(median(last_6_adjacent_5s_differences), 0)"
            ),
            "causality": "candidate-boundary and earlier observations only",
            "memory_label": "mysql_mysqld_working_set_mb",
            "plan_aggregation": ["mean", "max"],
        },
        "dynamic_shrinkage_by_benchmark": final_shrinkage,
    }
    joblib.dump(artifact, output_dir / "global_memory_model.joblib")

    for benchmark in ("H", "C", "CH"):
        benchmark_frame = candidates[candidates["benchmark"] == benchmark].reset_index(drop=True)
        benchmark_matrix, benchmark_columns = _feature_matrix(
            benchmark_frame, include_context=True
        )
        benchmark_target = np.maximum(
            benchmark_frame["target_memory_mb"].to_numpy(dtype=float)
            - benchmark_frame["observed_base_memory_mb"].to_numpy(dtype=float),
            0.0,
        )
        benchmark_model = _new_model(seed)
        benchmark_model.fit(benchmark_matrix, benchmark_target)
        joblib.dump(
            {
                **artifact,
                "model": benchmark_model,
                "feature_columns": benchmark_columns,
                "benchmark": benchmark,
            },
            output_dir / f"{benchmark.lower()}_memory_model.joblib",
        )

    candidates.to_csv(output_dir / "oof_predictions.csv", index=False)
    report = {
        "config": config,
        "thresholds": {
            "held_out_r2_min": R2_THRESHOLD,
            "top15_pairwise_inversion_rate_max": TOP15_INVERSION_THRESHOLD,
            "top15_required_comparable_labels": 15,
        },
        "evaluation": {
            "method": (
                "5-fold grouped OOF; group=benchmark:case; formal model trained only on "
                "exact C+CH; causal recent-drift baseline; fixed C/CH shrinkage=0.10; "
                "Top15 selected by predicted score"
            ),
            "overall_hard_gate": overall,
            "overall_hard_gate_scope": "exact timestamp aligned C + CH candidates",
            "baseline_comparison": {
                "description": (
                    "single candidate-boundary memory reading plus plan model with "
                    "historical C=0.01/CH=0.05 shrinkage"
                ),
                "metrics": baseline_overall,
                "absolute_improvement": {
                    "mae_mb": baseline_overall["mae_mb"] - overall["mae_mb"],
                    "rmse_mb": baseline_overall["rmse_mb"] - overall["rmse_mb"],
                    "p95_absolute_error_mb": (
                        baseline_overall["p95_absolute_error_mb"]
                        - overall["p95_absolute_error_mb"]
                    ),
                    "median_absolute_error_mb": (
                        baseline_overall["median_absolute_error_mb"]
                        - overall["median_absolute_error_mb"]
                    ),
                },
            },
            "all_candidate_diagnostic": all_candidate_diagnostic,
            "benchmark_diagnostics": by_benchmark,
            "error_diagnostics": error_diagnostics,
        },
        "data": {
            "candidate_count": len(candidates),
            "formal_exact_alignment_candidate_count": len(formal),
            "case_count": int(candidates["group"].nunique()),
            "counts_by_benchmark": {
                key: int(value)
                for key, value in candidates.groupby("benchmark").size().to_dict().items()
            },
            "skipped": list(skipped),
        },
        "artifacts": {
            "model": "global_memory_model.joblib",
            "per_benchmark_models": [
                "h_memory_model.joblib",
                "c_memory_model.joblib",
                "ch_memory_model.joblib",
            ],
            "oof_predictions": "oof_predictions.csv",
        },
        "feature_sets": {
            "baseline_feature_count": len(baseline_columns),
            "optimized_feature_count": len(feature_columns),
            "optimized_online_context": [
                "context_last_memory_mb",
                "context_positive_drift_mb_per_window",
                "context_block_index",
                "context_candidate_progress",
            ],
        },
    }
    (output_dir / "evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    parser.add_argument("--feature-dir", type=Path, default=DEFAULT_FEATURE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--block-rows", type=int, default=12)
    parser.add_argument("--window-s", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.block_rows < 2 or args.window_s < 1:
        raise ValueError("--block-rows must be >=2 and --window-s must be >=1")
    candidates, skipped = build_candidates(
        args.base_dir,
        args.feature_dir,
        block_rows=args.block_rows,
        window_s=args.window_s,
    )
    report = train_evaluate(
        candidates,
        seed=args.seed,
        output_dir=args.output_dir,
        skipped=skipped,
        config={
            "base_dir": str(args.base_dir),
            "feature_dir": str(args.feature_dir),
            "output_dir": str(args.output_dir),
            "block_rows": args.block_rows,
            "window_s": args.window_s,
            "candidate_duration_s": args.block_rows * args.window_s,
            "seed": args.seed,
            "model": "ExtraTreesRegressor",
        },
    )
    gate = report["evaluation"]["overall_hard_gate"]
    print(json.dumps(gate, ensure_ascii=False, indent=2), flush=True)
    if not gate["quality_gate_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
