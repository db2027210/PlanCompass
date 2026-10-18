from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parent
DEFAULT_FEATURE_CACHE_DIR = Path("/home/anonymous/program/features_cache_M1")
DEFAULT_OUTPUT_ROOT = ROOT / "config" / "generated" / "baseline_retrained"
SEED = 42
AP_MEMORY_REPLAY_HISTORY_LIMIT = max(
    8,
    int(os.environ.get("BONECT_AP_MEMORY_REPLAY_HISTORY_LIMIT", "40")),
)
AP_MEMORY_REPLAY_MIN_SAMPLES = max(
    5,
    int(os.environ.get("BONECT_AP_MEMORY_REPLAY_MIN_SAMPLES", "25")),
)

CPU_BASE_FEATURES = [
    "F_scan_tuple", "F_nl_tuple", "F_join_tuple", "F_agg_tuple",
    "F_sort_tuple", "F_materialize_tuple", "F_proj_tuple",
    "F_index_tuple", "F_hash_build", "F_hash_probe",
    "F_scan_filter", "F_index_cond", "F_join_filter",
    "F_hash_cond", "F_merge_cond", "F_agg_trans",
    "F_agg_final", "F_sort_cmp", "F_materialize_oper",
]

IO_BASE_FEATURES = [
    "io_N_page", "io_N_index_page", "io_Selectivity", "io_indexCorrelation",
    "io_pages_fetched", "io_T",
    "io_Outer_Pages", "io_Inner_Pages", "io_Outer_Rows",
    "io_npageaccesses", "io_npages", "io_nruns", "io_M",
    "io_pages_spill", "io_depth",
    "io_Outer_Sel_Ratio", "io_Inner_Sel_Ratio", "io_Spill_Pages",
    "io_I_idx_rand_pages", "io_I_idx_seq_part", "io_I_idx_rand_part",
    "io_I_bm_seq_part", "io_I_bm_rand_part",
    "io_I_sort_seq_part", "io_I_sort_rand_part",
    "io_I_spill_seq_part", "io_I_spill_rand_part", "io_I_hj_spill",
    "io_is_spill_sort", "io_is_spill_hashagg", "io_is_spill_hj", "io_is_spill_mat",
    "io_oper_seq_scan", "io_oper_idx_scan", "io_oper_bm_heap", "io_oper_bm_idx",
    "io_oper_sort", "io_oper_hashagg", "io_oper_hj", "io_oper_nl", "io_oper_mj", "io_oper_mat",
]

IO_DERIVED_FEATURES = [
    "io_pg_total_cost", "io_pg_run_cost", "io_tree_max_depth", "io_tree_node_count",
    "io_filter_ops", "io_ratio_npage_per_cost", "io_ratio_nipage_per_cost",
    "io_ratio_idx_vs_table", "io_ratio_outer_vs_inner_rows",
    "io_ratio_pages_per_row", "io_ratio_outer_vs_inner_pages",
    "io_ratio_run_vs_total_cost", "io_spill_pressure", "io_join_pressure",
    "io_index_pressure", "io_seq_scan_share", "io_bitmap_share", "io_cost_per_page",
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _relative_to_root(path: Path, root: Path = ROOT) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _validated_memory_registry(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return a usable frozen Memory registry without accepting an empty one."""
    registry = _read_json(path)
    models = registry.get("models") if isinstance(registry, dict) else None
    if not isinstance(models, dict) or not models:
        return {}, {"valid": False, "reason": "missing_or_empty_models"}

    selected_model_set = str(
        registry.get("default_model_set") or "TP_Hybrid"
    ).strip()
    model = models.get(selected_model_set)
    if not isinstance(model, dict):
        return {}, {
            "valid": False,
            "reason": f"missing_default_model:{selected_model_set}",
        }
    model_type = str(model.get("model_type") or "").strip()
    if not model_type:
        return {}, {"valid": False, "reason": "missing_model_type"}
    if model_type in {"constant", "linear"}:
        intercept = _safe_float(model.get("intercept"), float("nan"))
        if not math.isfinite(intercept):
            return {}, {"valid": False, "reason": "nonfinite_intercept"}
        features = model.get("features") or []
        coefficients = model.get("coef") or []
        if model_type == "linear" and len(features) != len(coefficients):
            return {}, {"valid": False, "reason": "linear_feature_coef_mismatch"}
    elif model_type.startswith("joblib") and not str(model.get("artifact_path") or "").strip():
        return {}, {"valid": False, "reason": "joblib_artifact_path_missing"}

    return registry, {
        "valid": True,
        "model_set": selected_model_set,
        "model_type": model_type,
        "path": str(path),
    }


def _resolve_path(path_value: Any, root: Path = ROOT) -> Path:
    path = Path(str(path_value or ""))
    return path if path.is_absolute() else root / path


def _ml_imports() -> Tuple[Any, Any, Any, Any, Any, Any, Any, Any]:
    thread_limit = str(max(1, abs(_resource_model_retrain_n_jobs())))
    for env_name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(env_name, thread_limit)

    import joblib  # type: ignore
    import numpy as np  # type: ignore
    import pandas as pd  # type: ignore
    from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor  # type: ignore
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score  # type: ignore
    from sklearn.model_selection import train_test_split  # type: ignore

    return (
        joblib,
        np,
        pd,
        ExtraTreesRegressor,
        HistGradientBoostingRegressor,
        RandomForestRegressor,
        mean_absolute_error,
        mean_squared_error,
        r2_score,
        train_test_split,
    )


def _error_bound_record(
    *,
    value: float,
    unit: str,
    source: str,
    quantile: Optional[float] = None,
    sample_count: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "value": float(max(0.0, value)),
        "unit": unit,
        "source": source,
    }
    if quantile is not None:
        record["quantile"] = float(quantile)
    if sample_count is not None:
        record["sample_count"] = int(sample_count)
    if extra:
        record.update(extra)
    return record


def _metric_error_bound(metrics: Dict[str, Any], unit: str) -> Dict[str, Any]:
    test = metrics.get("test") if isinstance(metrics.get("test"), dict) else {}
    value = _safe_float(
        test.get("abs_error_p95"),
        _safe_float(test.get("mae"), 0.0),
    )
    return _error_bound_record(
        value=value,
        unit=unit,
        source="test_abs_error_p95",
        quantile=0.95,
        sample_count=int(test.get("sample_count") or metrics.get("test_samples") or 0),
    )


def _pairwise_order_metrics(
    y_true: Any,
    y_pred: Any,
    groups: Any,
    np: Any,
) -> Dict[str, Any]:
    """Evaluate ordering only within the same replay/workload group.

    Absolute resource error can look small while the sign between two nearby
    candidates is reversed.  Candidate selection needs the latter.  Pairs from
    different workloads or resource profiles are intentionally excluded.
    """
    actual = np.asarray(y_true, dtype=float)
    predicted = np.asarray(y_pred, dtype=float)
    group_values = np.asarray(groups, dtype=object)
    if len(actual) != len(predicted) or len(actual) != len(group_values):
        raise ValueError("pairwise metric arrays must have equal length")

    pair_count = 0
    inversion_count = 0
    tie_count = 0
    signal_floor = 1e-12
    for left in range(len(actual)):
        for right in range(left + 1, len(actual)):
            if group_values[left] != group_values[right]:
                continue
            actual_delta = float(actual[left] - actual[right])
            predicted_delta = float(predicted[left] - predicted[right])
            if abs(actual_delta) <= signal_floor:
                continue
            pair_count += 1
            if abs(predicted_delta) <= signal_floor:
                tie_count += 1
            elif actual_delta * predicted_delta < 0.0:
                inversion_count += 1
    correct_count = pair_count - inversion_count - tie_count
    return {
        "pairwise_pair_count": int(pair_count),
        "pairwise_inversion_count": int(inversion_count),
        "pairwise_tie_count": int(tie_count),
        "pairwise_inversion_rate": (
            float(inversion_count / pair_count) if pair_count else None
        ),
        "pairwise_direction_accuracy": (
            float(correct_count / pair_count) if pair_count else None
        ),
    }


def _evaluate_predictions(
    y_true: Any,
    y_pred: Any,
    np: Any,
    mae_fn: Any,
    mse_fn: Any,
    r2_fn: Any,
    groups: Optional[Any] = None,
) -> Dict[str, Any]:
    y_true_arr = np.asarray(y_true, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)
    abs_err = np.abs(y_true_arr - y_pred_arr)
    rel_den = np.maximum(np.abs(y_true_arr), 1e-9)
    rel_err = abs_err / rel_den
    r2_value = 0.0
    if len(y_true_arr) >= 2:
        try:
            r2_value = float(r2_fn(y_true_arr, y_pred_arr))
        except Exception:
            r2_value = 0.0
    result: Dict[str, Any] = {
        "rmse": float(math.sqrt(mse_fn(y_true_arr, y_pred_arr))) if len(y_true_arr) else 0.0,
        "mae": float(mae_fn(y_true_arr, y_pred_arr)) if len(y_true_arr) else 0.0,
        "r2": r2_value,
        "abs_error_p95": float(np.quantile(abs_err, 0.95)) if len(abs_err) else 0.0,
        "abs_error_p90": float(np.quantile(abs_err, 0.90)) if len(abs_err) else 0.0,
        "abs_error_p50": float(np.quantile(abs_err, 0.50)) if len(abs_err) else 0.0,
        "abs_error_max": float(np.max(abs_err)) if len(abs_err) else 0.0,
        "relative_error_p95": float(np.quantile(rel_err, 0.95)) if len(rel_err) else 0.0,
        "relative_error_p90": float(np.quantile(rel_err, 0.90)) if len(rel_err) else 0.0,
        "relative_error_p50": float(np.quantile(rel_err, 0.50)) if len(rel_err) else 0.0,
        "relative_error_max": float(np.max(rel_err)) if len(rel_err) else 0.0,
        "sample_count": int(len(y_true_arr)),
    }
    if groups is not None:
        result.update(_pairwise_order_metrics(y_true_arr, y_pred_arr, groups, np))
    return result


def _grouped_holdout_indices(
    groups: Any,
    *,
    seed: int,
    np: Any,
    test_fraction: float = 0.20,
) -> Tuple[Any, Any, Dict[str, Any]]:
    """Split whole replay/workload groups, never adjacent rows from one group."""
    group_values = np.asarray(groups, dtype=object)
    unique_groups = sorted({str(value) for value in group_values.tolist()})
    if len(unique_groups) < 2:
        raise RuntimeError(
            "grouped resource-model holdout requires at least two independent groups"
        )
    rng = np.random.RandomState(int(seed))
    shuffled = list(unique_groups)
    rng.shuffle(shuffled)
    test_group_count = max(1, int(math.ceil(len(shuffled) * float(test_fraction))))
    test_group_count = min(test_group_count, len(shuffled) - 1)
    test_groups = set(shuffled[:test_group_count])
    test_mask = np.asarray(
        [str(value) in test_groups for value in group_values],
        dtype=bool,
    )
    train_idx = np.flatnonzero(~test_mask)
    test_idx = np.flatnonzero(test_mask)
    if len(train_idx) == 0 or len(test_idx) == 0:
        raise RuntimeError("grouped resource-model holdout produced an empty split")
    return train_idx, test_idx, {
        "split_policy": "grouped_holdout_v1",
        "group_count": len(unique_groups),
        "train_group_count": len(unique_groups) - len(test_groups),
        "test_group_count": len(test_groups),
        "test_groups": sorted(test_groups),
    }


def _clean_df(df: Any, np: Any) -> Any:
    return df.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _resource_model_retrain_n_jobs() -> int:
    raw_value = os.environ.get("RESOURCE_MODEL_RETRAIN_N_JOBS", "").strip()
    if raw_value:
        try:
            parsed = int(raw_value)
            if parsed != 0:
                return parsed
        except ValueError:
            pass
    cpu_count = os.cpu_count() or 1
    return max(1, min(8, cpu_count))


def _resource_model_retrain_env_int(name: str, default: int, min_value: int) -> int:
    raw_value = os.environ.get(name, "").strip()
    if raw_value:
        try:
            return max(min_value, int(raw_value))
        except ValueError:
            pass
    return int(default)


def _resource_model_retrain_tree_estimators() -> int:
    return _resource_model_retrain_env_int("RESOURCE_MODEL_RETRAIN_TREE_ESTIMATORS", 700, 10)


def _resource_model_retrain_hist_max_iter() -> int:
    return _resource_model_retrain_env_int("RESOURCE_MODEL_RETRAIN_HIST_MAX_ITER", 1200, 20)


def _cpu_features(df: Any, include_active: bool, np: Any) -> Any:
    cols = [c for c in CPU_BASE_FEATURES if c in df.columns]
    x = df[cols].astype(float).copy()
    if include_active:
        active = (
            df["active_sql_count"].astype(float).clip(lower=1.0)
            if "active_sql_count" in df.columns
            else x.iloc[:, 0].copy().mul(0.0).add(1.0) if len(cols) else None
        )
        if active is None:
            active = df.iloc[:, 0].copy().mul(0.0).add(1.0)
        x["active_sql_count"] = active
        x["log_active_sql_count"] = np.log1p(active)
        for col in cols:
            x[f"{col}_per_active"] = x[col] / active
    return _clean_df(x, np)


def _complete_io_features(df: Any, features: List[str], np: Any) -> Any:
    pd = __import__("pandas")
    x = pd.DataFrame(index=df.index)
    for col in features:
        x[col] = df[col].astype(float) if col in df.columns else 0.0
    return _clean_df(x, np)


def _fit_regressor(
    x: Any,
    y: Any,
    *,
    model_name: str,
    log_target: bool,
    seed: int,
    np: Any,
    ExtraTreesRegressor: Any,
    HistGradientBoostingRegressor: Any,
    RandomForestRegressor: Any,
    mean_absolute_error: Any,
    mean_squared_error: Any,
    r2_score: Any,
    train_test_split: Any,
    groups: Optional[Any] = None,
) -> Tuple[Any, Dict[str, Any]]:
    idx = np.arange(len(y))
    if groups is None:
        train_idx, test_idx = train_test_split(idx, test_size=0.2, random_state=seed)
        split_audit = {
            "split_policy": "row_random_holdout_legacy",
            "group_count": 0,
            "train_group_count": 0,
            "test_group_count": 0,
        }
        group_array = None
    else:
        group_array = np.asarray(groups, dtype=object)
        if len(group_array) != len(y):
            raise ValueError("resource-model groups must match target row count")
        train_idx, test_idx, split_audit = _grouped_holdout_indices(
            group_array,
            seed=seed,
            np=np,
        )
    x_train, x_test = x.iloc[train_idx], x.iloc[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]

    if model_name == "hist_gb":
        model = HistGradientBoostingRegressor(
            learning_rate=0.04,
            max_depth=8,
            max_iter=_resource_model_retrain_hist_max_iter(),
            min_samples_leaf=15,
            random_state=seed,
        )
    elif model_name == "rf":
        model = RandomForestRegressor(
            n_estimators=_resource_model_retrain_tree_estimators(),
            max_depth=24,
            min_samples_leaf=2,
            random_state=seed,
            n_jobs=_resource_model_retrain_n_jobs(),
        )
    else:
        model = ExtraTreesRegressor(
            n_estimators=_resource_model_retrain_tree_estimators(),
            max_depth=None,
            min_samples_leaf=4,
            random_state=seed,
            n_jobs=_resource_model_retrain_n_jobs(),
        )

    y_fit = np.log1p(np.clip(y_train, 0, None)) if log_target else y_train
    model.fit(x_train, y_fit)
    pred_train = model.predict(x_train)
    pred_test = model.predict(x_test)
    if log_target:
        pred_train = np.expm1(pred_train)
        pred_test = np.expm1(pred_test)
    pred_train = np.clip(pred_train, 0, None)
    pred_test = np.clip(pred_test, 0, None)
    metrics = {
        "train": _evaluate_predictions(
            y_train,
            pred_train,
            np,
            mean_absolute_error,
            mean_squared_error,
            r2_score,
            None if group_array is None else group_array[train_idx],
        ),
        "test": _evaluate_predictions(
            y_test,
            pred_test,
            np,
            mean_absolute_error,
            mean_squared_error,
            r2_score,
            None if group_array is None else group_array[test_idx],
        ),
        "train_samples": int(len(train_idx)),
        "test_samples": int(len(test_idx)),
        "resource_model_retrain_n_jobs": int(_resource_model_retrain_n_jobs()),
        "resource_model_retrain_tree_estimators": int(_resource_model_retrain_tree_estimators()),
        "resource_model_retrain_hist_max_iter": int(_resource_model_retrain_hist_max_iter()),
        **split_audit,
    }
    return model, metrics


def _save_joblib_artifact(
    *,
    output_dir: Path,
    filename: str,
    model: Any,
    features: List[str],
    model_type: str,
    target_transform: Optional[str],
    metrics: Dict[str, Any],
    unit: str,
    joblib: Any,
) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    payload = {
        "model": model,
        "features": features,
        "model_type": model_type,
        "target_transform": target_transform,
        "clip_min": 0.0,
        "metrics": metrics,
        "error_bound": _metric_error_bound(metrics, unit),
    }
    joblib.dump(payload, path)
    return _relative_to_root(path)


def _load_cpu_dataset(data_dir: Path, pattern: str, pd: Any, np: Any) -> Any:
    frames = []
    for path in sorted(data_dir.glob(pattern)):
        df = pd.read_parquet(path)
        if "target_cpu" not in df.columns:
            continue
        df = df.copy()
        df["source_file"] = path.name
        frames.append(df)
    if not frames:
        raise RuntimeError(f"no CPU parquet files matched {pattern}")
    out = _clean_df(pd.concat(frames, ignore_index=True), np)
    return out[out["target_cpu"] > 0].reset_index(drop=True)


def _export_cpu_artifacts(
    *,
    registry_path: Path,
    data_dir: Path,
    artifact_dir: Path,
    seed: int,
) -> Dict[str, Any]:
    (
        joblib,
        np,
        pd,
        ExtraTreesRegressor,
        HistGradientBoostingRegressor,
        RandomForestRegressor,
        mean_absolute_error,
        mean_squared_error,
        r2_score,
        train_test_split,
    ) = _ml_imports()
    registry = _read_json(registry_path)
    models = registry.setdefault("models", {})
    summary: Dict[str, Any] = {}

    specs = [
        ("H_serial", "h*_serial_features.parquet", False, "extra"),
        ("C_only", "c*_5s_features.parquet", True, "hist_gb"),
        ("CH_only", "ch*_5s_features.parquet", True, "hist_gb"),
    ]
    for model_set, pattern, include_active, model_name in specs:
        if model_set == "C_only":
            files = [
                p for p in data_dir.glob(pattern)
                if re.match(r"^c\d+_5s_features\.parquet$", p.name)
            ]
            if not files:
                raise RuntimeError("no C-only CPU parquet files found")
            frames = []
            for path in sorted(files):
                frame = pd.read_parquet(path).copy()
                frame["source_file"] = path.name
                frames.append(frame)
            df = _clean_df(pd.concat(frames, ignore_index=True), np)
            df = df[df["target_cpu"] > 0].reset_index(drop=True)
        else:
            df = _load_cpu_dataset(data_dir, pattern, pd, np)
        x = _cpu_features(df, include_active=include_active, np=np)
        y = df["target_cpu"].astype(float).to_numpy()
        groups = df["source_file"].astype(str).to_numpy()
        model, metrics = _fit_regressor(
            x,
            y,
            model_name=model_name,
            log_target=(model_set != "H_serial"),
            seed=seed,
            np=np,
            ExtraTreesRegressor=ExtraTreesRegressor,
            HistGradientBoostingRegressor=HistGradientBoostingRegressor,
            RandomForestRegressor=RandomForestRegressor,
            mean_absolute_error=mean_absolute_error,
            mean_squared_error=mean_squared_error,
            r2_score=r2_score,
            train_test_split=train_test_split,
            groups=groups,
        )
        artifact_path = _save_joblib_artifact(
            output_dir=artifact_dir,
            filename=f"cpu_{model_set}.joblib",
            model=model,
            features=list(x.columns),
            model_type=f"joblib_{model_name}",
            target_transform="log1p_expm1" if model_set != "H_serial" else None,
            metrics=metrics,
            unit="cpu_seconds_per_window",
            joblib=joblib,
        )
        rec = models.setdefault(model_set, {})
        rec["artifact_path"] = artifact_path
        rec["model_type"] = f"joblib_{model_name}"
        rec["features"] = list(x.columns)
        rec["target_transform"] = "log1p_expm1" if model_set != "H_serial" else None
        rec["metrics"] = metrics
        rec["error_bound"] = _metric_error_bound(metrics, "cpu_seconds_per_window")
        summary[model_set] = metrics

    registry["artifact_export"] = {
        "script": _relative_to_root(Path(__file__)),
        "output_dir": _relative_to_root(artifact_dir),
        "generated_at": int(time.time()),
    }
    _write_json(registry_path, registry)
    return summary


def _load_io_dataset(data_dir: Path, family: str, pd: Any, np: Any) -> Any:
    if family == "H":
        cpu_files = sorted(data_dir.glob("h*_serial_features.parquet"))
        io_map = {p.name.split("_io_")[0]: p for p in data_dir.glob("h*_io_serial_features.parquet")}
        frames = []
        for cpu_path in cpu_files:
            case = cpu_path.name.split("_serial_")[0]
            io_path = io_map.get(case)
            if io_path is None:
                continue
            cpu_df = pd.read_parquet(cpu_path)
            io_df = pd.read_parquet(io_path)
            dup = [c for c in io_df.columns if c in cpu_df.columns]
            frame = pd.concat([cpu_df, io_df.drop(columns=dup, errors="ignore")], axis=1)
            frame["_source_group"] = case
            frames.append(frame)
        if not frames:
            raise RuntimeError("no H IO parquet pairs found")
        return _clean_df(pd.concat(frames, ignore_index=True), np)
    if family == "C":
        files = [
            p for p in data_dir.glob("c*_io_5s_features.parquet")
            if re.match(r"^c\d+_io_5s_features\.parquet$", p.name)
        ]
    else:
        files = [
            p for p in data_dir.glob("ch*_io_5s_features.parquet")
            if re.match(r"^ch\d+_io_5s_features\.parquet$", p.name)
        ]
    if not files:
        raise RuntimeError(f"no {family} IO parquet files found")
    frames = []
    for path in sorted(files):
        frame = pd.read_parquet(path).copy()
        frame["_source_group"] = path.name
        frames.append(frame)
    return _clean_df(pd.concat(frames, ignore_index=True), np)


def _export_io_artifacts(
    *,
    registry_path: Path,
    data_dir: Path,
    artifact_dir: Path,
    seed: int,
) -> Dict[str, Any]:
    (
        joblib,
        np,
        pd,
        ExtraTreesRegressor,
        HistGradientBoostingRegressor,
        RandomForestRegressor,
        mean_absolute_error,
        mean_squared_error,
        r2_score,
        train_test_split,
    ) = _ml_imports()
    registry = _read_json(registry_path)
    models = registry.setdefault("models", {})
    target_map = {
        "total": "target_io_mb",
        "read": "target_io_read_mb",
        "write": "target_io_write_mb",
    }
    feature_union = sorted(set(IO_BASE_FEATURES) | set(IO_DERIVED_FEATURES))
    summary: Dict[str, Any] = {}
    for family in ["H", "C", "CH"]:
        df = _load_io_dataset(data_dir, family, pd, np)
        group = models.setdefault(family, {}).setdefault("targets", {})
        summary[family] = {}
        for target_name, target_col in target_map.items():
            if target_col not in df.columns:
                continue
            cur = df[df[target_col] > 0].reset_index(drop=True)
            if len(cur) < 20:
                continue
            x = _complete_io_features(cur, feature_union, np)
            y = cur[target_col].astype(float).to_numpy()
            groups = cur["_source_group"].astype(str).to_numpy()
            model_name = "hist_gb"
            if family == "CH" and target_name in {"total", "write"}:
                model_name = "extra"
            if family == "CH" and target_name == "read":
                model_name = "rf"
            model, metrics = _fit_regressor(
                x,
                y,
                model_name=model_name,
                log_target=False,
                seed=seed,
                np=np,
                ExtraTreesRegressor=ExtraTreesRegressor,
                HistGradientBoostingRegressor=HistGradientBoostingRegressor,
                RandomForestRegressor=RandomForestRegressor,
                mean_absolute_error=mean_absolute_error,
                mean_squared_error=mean_squared_error,
                r2_score=r2_score,
                train_test_split=train_test_split,
                groups=groups,
            )
            artifact_path = _save_joblib_artifact(
                output_dir=artifact_dir,
                filename=f"io_{family}_{target_name}.joblib",
                model=model,
                features=list(x.columns),
                model_type=f"joblib_{model_name}",
                target_transform=None,
                metrics=metrics,
                unit="MB/window",
                joblib=joblib,
            )
            rec = group.setdefault(target_name, {})
            rec["artifact_path"] = artifact_path
            rec["model_type"] = f"joblib_{model_name}"
            rec["features"] = list(x.columns)
            rec["target_transform"] = None
            rec["metrics"] = metrics
            rec["error_bound"] = _metric_error_bound(metrics, "MB/window")
            summary[family][target_name] = metrics

    registry["artifact_export"] = {
        "script": _relative_to_root(Path(__file__)),
        "output_dir": _relative_to_root(artifact_dir),
        "generated_at": int(time.time()),
    }
    _write_json(registry_path, registry)
    return summary


def _export_memory_artifacts(
    *,
    registry_path: Path,
    data_dir: Path,
    artifact_dir: Path,
    root: Path,
    seed: int,
) -> Dict[str, Any]:
    """Train CH/TP and AP-only memory models from real runtime labels."""
    (
        joblib,
        np,
        pd,
        ExtraTreesRegressor,
        HistGradientBoostingRegressor,
        RandomForestRegressor,
        mean_absolute_error,
        mean_squared_error,
        r2_score,
        train_test_split,
    ) = _ml_imports()
    files = [
        path
        for path in data_dir.glob("ch*_5s_weighted_runtime_features.parquet")
        if re.match(r"^ch\d+_5s_weighted_runtime_features\.parquet$", path.name)
    ]
    if not files:
        raise RuntimeError("no CH runtime-labelled Memory parquet files found")

    target_col = "R_memory_working_set_mb_mean"
    frames = []
    for path in sorted(files):
        frame = pd.read_parquet(path)
        if target_col in frame.columns:
            frame = frame.copy()
            frame["_source_group"] = path.name
            frames.append(frame)
    if not frames:
        raise RuntimeError(f"Memory training target {target_col!r} is missing")

    df = _clean_df(pd.concat(frames, ignore_index=True), np)
    df = df[df[target_col] > 0].reset_index(drop=True)
    if len(df) < 25:
        raise RuntimeError(f"insufficient Memory train/test samples: {len(df)}")

    x = _cpu_features(df, include_active=True, np=np)
    y = df[target_col].astype(float).to_numpy()
    groups = df["_source_group"].astype(str).to_numpy()
    model, metrics = _fit_regressor(
        x,
        y,
        model_name="extra",
        log_target=False,
        seed=seed,
        np=np,
        ExtraTreesRegressor=ExtraTreesRegressor,
        HistGradientBoostingRegressor=HistGradientBoostingRegressor,
        RandomForestRegressor=RandomForestRegressor,
        mean_absolute_error=mean_absolute_error,
        mean_squared_error=mean_squared_error,
        r2_score=r2_score,
        train_test_split=train_test_split,
        groups=groups,
    )
    artifact_path = _save_joblib_artifact(
        output_dir=artifact_dir,
        filename="memory_TP_Hybrid.joblib",
        model=model,
        features=list(x.columns),
        model_type="joblib_extra",
        target_transform=None,
        metrics=metrics,
        unit="MB",
        joblib=joblib,
    )

    registry = _read_json(registry_path)
    tp_model = registry.setdefault("models", {}).setdefault("TP_Hybrid", {})
    tp_model.update(
        {
            "artifact_path": artifact_path,
            "model_type": "joblib_extra",
            "target": "actual_memory_mb",
            "target_runtime_column": target_col,
            "features": list(x.columns),
            "target_transform": None,
            "metrics": metrics,
            "error_bound": _metric_error_bound(metrics, "MB"),
        }
    )
    registry["artifact_export"] = {
        "script": _relative_to_root(Path(__file__)),
        "output_dir": _relative_to_root(artifact_dir),
        "generated_at": int(time.time()),
        "source": "offline_ch_runtime_memory_train_test",
        "source_file_count": len(files),
        "sample_count": len(df),
    }
    tp_summary = {
        "model_type": "joblib_extra",
        "artifact_path": artifact_path,
        "metrics": metrics,
        "error_bound": tp_model["error_bound"],
    }

    ap_summary = _export_ap_memory_replay_artifact(
        registry=registry,
        root=root,
        artifact_dir=artifact_dir,
        seed=seed,
        ml_imports=(
            joblib,
            np,
            pd,
            ExtraTreesRegressor,
            HistGradientBoostingRegressor,
            RandomForestRegressor,
            mean_absolute_error,
            mean_squared_error,
            r2_score,
            train_test_split,
        ),
    )
    registry["artifact_export"]["ap_replay_sample_count"] = ap_summary["sample_count"]
    registry["artifact_export"]["ap_replay_source_file_count"] = ap_summary["source_file_count"]
    _write_json(registry_path, registry)

    return {
        "status": "retrained",
        "source": "runtime_memory_train_test",
        "path": _relative_to_root(registry_path, ROOT),
        "selected_model_set": "TP_Hybrid",
        "updated_model_sets": {
            "AP_only": ap_summary["model_summary"],
            "TP_Hybrid": tp_summary,
        },
        "source_file_count": len(files),
        "sample_count": len(df),
        "ap_replay_source_file_count": ap_summary["source_file_count"],
        "ap_replay_sample_count": ap_summary["sample_count"],
    }


def _export_ap_memory_replay_artifact(
    *,
    registry: Dict[str, Any],
    root: Path,
    artifact_dir: Path,
    seed: int,
    ml_imports: Tuple[Any, Any, Any, Any, Any, Any, Any, Any, Any, Any],
) -> Dict[str, Any]:
    """Build AP-only training rows from prior real VM replay plus captured plans."""
    (
        joblib,
        np,
        pd,
        ExtraTreesRegressor,
        HistGradientBoostingRegressor,
        RandomForestRegressor,
        mean_absolute_error,
        mean_squared_error,
        r2_score,
        train_test_split,
    ) = ml_imports
    from plan_resource_estimator import PlanResourceEstimator

    history_root = root / "history" / "pg_benchmark_bottleneck_matrix"
    history_files = sorted(
        history_root.glob(
            "pg_bonect_phase2*/memory/hybench_ap_optimizer_history_*.jsonl"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    estimator = PlanResourceEstimator(
        pg_host="",
        pg_port="",
        pg_user="",
        pg_dbname="",
        estimator_cfg={
            "shared": {"work_mem_bytes": 32 * 1024 * 1024},
            "memory": {
                "overlap_factor": 0.6,
                "beta_sort": 1.0,
                "beta_hash": 1.0,
                "beta_hashagg": 1.0,
                "beta_materialize": 1.0,
                "beta_memoize": 0.0,
            },
        },
    )
    rows: List[Dict[str, Any]] = []
    source_files: List[str] = []
    seen_capture_paths = set()
    for history_path in history_files[:AP_MEMORY_REPLAY_HISTORY_LIMIT]:
        before_count = len(rows)
        with history_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") != "feedback_final_validation":
                    continue
                validation = event.get("final_validation") or {}
                memory_bytes = _safe_float(
                    (validation.get("resource_raw") or {}).get("memory"),
                    0.0,
                )
                capture_value = event.get("final_plan_capture_file") or (
                    (validation.get("plan_change_summary") or {}).get(
                        "final_plan_capture_file"
                    )
                )
                capture_path = _resolve_path(capture_value, root)
                capture_key = str(capture_path.resolve())
                if (
                    memory_bytes <= 0.0
                    or not capture_value
                    or not capture_path.exists()
                    or capture_key in seen_capture_paths
                ):
                    continue
                capture = _read_json(capture_path)
                query_estimates: List[Dict[str, Any]] = []
                for item in capture.get("items") or []:
                    raw_plan = item.get("raw_plan_json") or []
                    plan_doc = raw_plan[0] if isinstance(raw_plan, list) and raw_plan else raw_plan
                    plan_root = plan_doc.get("Plan") if isinstance(plan_doc, dict) else None
                    if not isinstance(plan_root, dict):
                        continue
                    node_features = estimator._extract_plan_node_features(plan_root)
                    query_text = str(item.get("query") or "")
                    query_resources = estimator._estimate_query_resources(
                        query_text=query_text,
                        node_features=node_features,
                        workload_context={"workload_family": "H"},
                    )
                    query_estimates.append(
                        {
                            "nodes": node_features,
                            "replay_count": 1,
                            "query": query_text,
                            "statement_kind": "select",
                            "query_resources": query_resources,
                        }
                    )
                if not query_estimates:
                    continue

                total_replay = float(len(query_estimates))
                weighted_memory_bytes = sum(
                    _safe_float(
                        ((item.get("query_resources") or {}).get("memory") or {}).get(
                            "mem_query_parallel_bytes"
                        ),
                        0.0,
                    )
                    for item in query_estimates
                ) / total_replay
                weighted_derived_mb = sum(
                    _safe_float(
                        ((item.get("query_resources") or {}).get("memory") or {}).get(
                            "derived_memory_mb"
                        ),
                        0.0,
                    )
                    for item in query_estimates
                ) / total_replay
                ap_concurrency = 8.0
                weighted_memory_mb = weighted_memory_bytes / (1024.0 ** 2)
                features = estimator._aggregate_cpu_model_features(
                    query_estimates,
                    {"active_sql_count": ap_concurrency, "workload_family": "H"},
                )
                features.update(
                    {
                        "AP_E_mem_mb": weighted_memory_mb,
                        "AP_Concurrency": ap_concurrency,
                        "AP_E_mem_x_AP_Concurrency": weighted_memory_mb * ap_concurrency,
                        "derived": weighted_derived_mb * ap_concurrency,
                        "weighted_derived_memory_mb": weighted_derived_mb,
                        "weighted_memory_mb": weighted_memory_mb,
                        "concurrent_working_memory_mb": (
                            weighted_memory_mb * ap_concurrency * 0.6
                        ),
                        "actual_memory_mb": memory_bytes / (1024.0 ** 2),
                        "_source_group": _relative_to_root(history_path, root),
                    }
                )
                rows.append(features)
                seen_capture_paths.add(capture_key)
        if len(rows) > before_count:
            source_files.append(_relative_to_root(history_path, root))
        if len(rows) >= 30:
            break

    if len(rows) < AP_MEMORY_REPLAY_MIN_SAMPLES:
        raise RuntimeError(
            "insufficient AP Memory replay train/test samples: "
            f"{len(rows)} < {AP_MEMORY_REPLAY_MIN_SAMPLES} "
            f"(history_limit={AP_MEMORY_REPLAY_HISTORY_LIMIT})"
        )
    frame = _clean_df(pd.DataFrame(rows), np)
    y = frame.pop("actual_memory_mb").astype(float).to_numpy()
    groups = frame.pop("_source_group").astype(str).to_numpy()
    model, metrics = _fit_regressor(
        frame,
        y,
        model_name="extra",
        log_target=False,
        seed=seed,
        np=np,
        ExtraTreesRegressor=ExtraTreesRegressor,
        HistGradientBoostingRegressor=HistGradientBoostingRegressor,
        RandomForestRegressor=RandomForestRegressor,
        mean_absolute_error=mean_absolute_error,
        mean_squared_error=mean_squared_error,
        r2_score=r2_score,
        train_test_split=train_test_split,
        groups=groups,
    )
    artifact_path = _save_joblib_artifact(
        output_dir=artifact_dir,
        filename="memory_AP_only.joblib",
        model=model,
        features=list(frame.columns),
        model_type="joblib_extra",
        target_transform=None,
        metrics=metrics,
        unit="MB",
        joblib=joblib,
    )
    ap_model = registry.setdefault("models", {}).setdefault("AP_only", {})
    ap_model.update(
        {
            "artifact_path": artifact_path,
            "model_type": "joblib_extra",
            "target": "actual_memory_mb",
            "features": list(frame.columns),
            "target_transform": None,
            "metrics": metrics,
            "error_bound": _metric_error_bound(metrics, "MB"),
            "training_source": "phase2_hybench_ap_real_vm_replay",
            "training_source_files": source_files,
        }
    )
    return {
        "source_file_count": len(source_files),
        "sample_count": len(frame),
        "model_summary": {
            "model_type": "joblib_extra",
            "artifact_path": artifact_path,
            "metrics": metrics,
            "error_bound": ap_model["error_bound"],
        },
    }


def _memory_pending_retrain_record(registry_path: Path, root: Path) -> Dict[str, Any]:
    registry = _read_json(registry_path)
    registry["artifact_export"] = {
        "script": _relative_to_root(Path(__file__)),
        "generated_at": int(time.time()),
        "source": "pending_baseline_memory_retrain",
        "note": (
            "Memory does not use copied historical training results. "
            "It must be retrained from the current baseline before optimization starts."
        ),
    }
    _write_json(registry_path, registry)
    return {
        "status": "pending_baseline_retrain",
        "path": _relative_to_root(registry_path, root),
        "requires": [
            "baseline_actual_normalized.memory",
            "baseline_estimation_metadata.resource_breakdown.memory.calibration_breakdown.learned_model",
        ],
    }


def _single_sample_memory_metrics(actual_mb: float, predicted_mb: float) -> Dict[str, Any]:
    abs_error = abs(float(actual_mb) - float(predicted_mb))
    rel_error = abs_error / max(abs(float(actual_mb)), 1e-9)
    sample = {
        "rmse": abs_error,
        "mae": abs_error,
        "r2": 0.0,
        "abs_error_p95": abs_error,
        "abs_error_p90": abs_error,
        "abs_error_p50": abs_error,
        "abs_error_max": abs_error,
        "relative_error_p95": rel_error,
        "relative_error_p90": rel_error,
        "relative_error_p50": rel_error,
        "relative_error_max": rel_error,
        "sample_count": 1,
    }
    return {
        "train": sample,
        "test": {
            "rmse": 0.0,
            "mae": 0.0,
            "r2": 0.0,
            "abs_error_p95": 0.0,
            "abs_error_p90": 0.0,
            "abs_error_p50": 0.0,
            "abs_error_max": 0.0,
            "relative_error_p95": 0.0,
            "relative_error_p90": 0.0,
            "relative_error_p50": 0.0,
            "relative_error_max": 0.0,
            "sample_count": 0,
        },
        "train_samples": 1,
        "test_samples": 0,
        "source": "baseline_single_sample_retrain",
    }


def _predict_memory_before_error_bound(model: Dict[str, Any], features: Dict[str, float]) -> Optional[float]:
    pred = _safe_float(model.get("intercept"), 0.0)
    model_features = list(model.get("features") or [])
    coef = list(model.get("coef") or [])
    if model.get("model_type") != "constant":
        if not model_features or len(model_features) != len(coef):
            return None
        for idx, feature in enumerate(model_features):
            pred += _safe_float(coef[idx], 0.0) * _safe_float(features.get(feature), 0.0)
    clip_min = model.get("clip_min")
    if clip_min not in (None, ""):
        pred = max(_safe_float(clip_min), pred)
    return pred


def retrain_memory_model_from_baseline(
    *,
    memory_json_path: Any,
    baseline_actual_normalized: Dict[str, float],
    baseline_estimation_metadata: Dict[str, Any],
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = root or ROOT
    registry_path = _resolve_path(memory_json_path, root)
    registry = _read_json(registry_path)
    if not registry:
        raise RuntimeError(f"memory model registry is missing or invalid: {registry_path}")
    models = registry.get("models") or {}
    if not isinstance(models, dict) or not models:
        raise RuntimeError(f"memory model registry has no models: {registry_path}")
    if "memory" not in baseline_actual_normalized:
        raise RuntimeError("memory baseline retrain requires baseline_actual_normalized['memory']")

    resource_breakdown = baseline_estimation_metadata.get("resource_breakdown") or {}
    learned = (((resource_breakdown.get("memory") or {}).get("calibration_breakdown") or {}).get("learned_model") or {})
    if not isinstance(learned, dict) or not learned:
        raise RuntimeError("memory baseline retrain requires baseline learned_model metadata")

    actual_mb = _safe_float(baseline_actual_normalized.get("memory"), 0.0) * 1024.0
    if actual_mb <= 0.0:
        raise RuntimeError(f"memory baseline actual must be positive, got {actual_mb:.6f} MB")

    selected_model_set = str(learned.get("model_set") or registry.get("default_model_set") or "TP_Hybrid")
    learned_features = learned.get("features") if isinstance(learned.get("features"), dict) else {}
    features = {str(k): _safe_float(v, 0.0) for k, v in learned_features.items()}
    selected_pred_before = _safe_float(
        learned.get("prediction_before_error_bound"),
        _safe_float(learned.get("prediction_memory_mb"), 0.0),
    )

    updated: Dict[str, Any] = {}
    generated_at = int(time.time())
    for model_set, model_obj in models.items():
        if not isinstance(model_obj, dict):
            continue
        model = model_obj
        model_features = list(model.get("features") or [])
        coef = list(model.get("coef") or [])
        old_intercept = _safe_float(model.get("intercept"), 0.0)
        if str(model_set) == selected_model_set and selected_pred_before > 0.0:
            pred_before = selected_pred_before
        else:
            pred_before = _predict_memory_before_error_bound(model, features)
        if pred_before is None:
            pred_before = old_intercept

        delta = actual_mb - pred_before
        if model.get("model_type") == "constant" or not model_features:
            model["model_type"] = "constant"
            model["features"] = []
            model["coef"] = []
            model["intercept"] = actual_mb
        elif len(model_features) == len(coef):
            model["intercept"] = old_intercept + delta
        else:
            model["model_type"] = "constant"
            model["features"] = []
            model["coef"] = []
            model["intercept"] = actual_mb

        clip_min = model.get("clip_min")
        if clip_min not in (None, "") and _safe_float(clip_min, 0.0) > actual_mb:
            model["clip_min"] = actual_mb

        pred_after = _predict_memory_before_error_bound(model, features)
        if pred_after is None:
            pred_after = actual_mb
        model["target"] = "actual_memory_mb"
        model["metrics"] = _single_sample_memory_metrics(actual_mb, pred_after)
        model["error_bound"] = _error_bound_record(
            value=abs(delta),
            unit="MB",
            source="baseline_retrain_abs_intercept_delta",
            sample_count=1,
            extra={
                "actual_memory_mb": actual_mb,
                "predicted_before_retrain_mb": pred_before,
                "predicted_after_retrain_before_error_bound_mb": pred_after,
                "intercept_delta_mb": delta,
                "selected_model_set": selected_model_set,
            },
        )
        model["baseline_training"] = {
            "source": "current_baseline_actual",
            "method": "intercept_retrain_preserve_plan_memory_features",
            "generated_at": generated_at,
            "actual_memory_mb": actual_mb,
            "predicted_before_retrain_mb": pred_before,
            "predicted_after_retrain_before_error_bound_mb": pred_after,
            "intercept_before_retrain": old_intercept,
            "intercept_after_retrain": _safe_float(model.get("intercept"), 0.0),
            "selected_model_set": selected_model_set,
            "feature_count": len(features),
            "feature_snapshot_top10": sorted(
                (
                    {"feature": name, "value": value}
                    for name, value in features.items()
                ),
                key=lambda item: abs(item["value"]),
                reverse=True,
            )[:10],
        }
        updated[str(model_set)] = {
            "model_type": model.get("model_type"),
            "intercept_before_retrain": old_intercept,
            "intercept_after_retrain": _safe_float(model.get("intercept"), 0.0),
            "predicted_before_retrain_mb": pred_before,
            "predicted_after_retrain_before_error_bound_mb": pred_after,
            "error_bound": model["error_bound"],
            "metrics": model["metrics"],
        }

    if not updated:
        raise RuntimeError("memory baseline retrain did not update any model set")

    registry["artifact_export"] = {
        "script": _relative_to_root(Path(__file__)),
        "generated_at": generated_at,
        "source": "baseline_memory_single_sample_retrain",
        "baseline_actual_memory_mb": actual_mb,
        "selected_model_set": selected_model_set,
        "note": (
            "Memory artifact was regenerated after the current baseline. "
            "Existing historical validation results are not used as training completion evidence. "
            "Path, Hash, Sort, and derived concurrency features are preserved; "
            "the baseline actual value retrains the model intercept."
        ),
    }
    _write_json(registry_path, registry)
    return {
        "status": "retrained",
        "source": "baseline_memory_single_sample_retrain",
        "path": _relative_to_root(registry_path, root),
        "actual_memory_mb": actual_mb,
        "selected_model_set": selected_model_set,
        "updated_model_sets": updated,
    }


def retrain_resource_models(
    *,
    root: Optional[Path] = None,
    feature_cache_dir: Optional[Path] = None,
    output_root: Optional[Path] = None,
    run_id: Optional[str] = None,
    seed: int = SEED,
    pretrained_bundle_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    root = root or ROOT
    feature_cache_dir = feature_cache_dir or DEFAULT_FEATURE_CACHE_DIR
    data_dir = feature_cache_dir / "data"
    run_id = run_id or time.strftime("baseline_%Y%m%d_%H%M%S")
    output_root = output_root or DEFAULT_OUTPUT_ROOT
    output_dir = output_root / run_id
    artifact_dir = output_dir / "model_artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)

    source_paths = {
        "cpu": root / "config" / "generated" / "cpu_resource_models.json",
        "io": root / "config" / "generated" / "io_resource_models.json",
        "memory": root / "config" / "generated" / "memory_resource_models.json",
    }
    registry_paths = {
        "cpu": output_dir / "cpu_resource_models.json",
        "io": output_dir / "io_resource_models.json",
        "memory": output_dir / "memory_resource_models.json",
    }
    if pretrained_bundle_dir is not None:
        bundle_dir = pretrained_bundle_dir
        if not bundle_dir.is_absolute():
            bundle_dir = root / bundle_dir
        bundle_summary_path = bundle_dir / "resource_model_retrain_summary.json"
        bundle_summary = _read_json(bundle_summary_path)
        if not bundle_summary:
            raise RuntimeError(
                f"pretrained resource-model bundle summary is missing or invalid: {bundle_summary_path}"
            )
        resources = bundle_summary.get("resources")
        if not isinstance(resources, dict) or any(
            name not in resources for name in ("cpu", "io", "memory")
        ):
            raise RuntimeError(
                "pretrained resource-model bundle must contain CPU/IO/Memory summaries"
            )
        for key, destination in registry_paths.items():
            source = bundle_dir / f"{key}_resource_models.json"
            if not source.is_file():
                raise RuntimeError(f"pretrained resource-model registry is missing: {source}")
            shutil.copyfile(source, destination)
        summary = copy.deepcopy(bundle_summary)
        summary.update(
            {
                "run_id": run_id,
                "output_dir": _relative_to_root(output_dir, root),
                "paths": {
                    "cpu_json": _relative_to_root(registry_paths["cpu"], root),
                    "io_json": _relative_to_root(registry_paths["io"], root),
                    "memory_json": _relative_to_root(registry_paths["memory"], root),
                },
                "pretrained_bundle_import": {
                    "source_dir": str(bundle_dir),
                    "source_summary": str(bundle_summary_path),
                    "mode": "benchmark_scoped_grouped_lovo_bundle",
                },
            }
        )
        _write_json(output_dir / "resource_model_retrain_summary.json", summary)
        return summary

    memory_fallback_registry, memory_fallback_validation = _validated_memory_registry(
        source_paths["memory"]
    )
    memory_fallback_source = str(source_paths["memory"])
    if not memory_fallback_validation.get("valid"):
        existing_memory_registry, existing_memory_validation = _validated_memory_registry(
            registry_paths["memory"]
        )
        if existing_memory_validation.get("valid"):
            memory_fallback_registry = existing_memory_registry
            memory_fallback_validation = existing_memory_validation
            memory_fallback_source = str(registry_paths["memory"])
    if not memory_fallback_validation.get("valid"):
        # The optimizer may pass its repair/ directory as root.  In that
        # layout, use the repository's sealed Memory registry as the
        # benchmark-scoped frozen fallback, without changing CPU or IO.
        bundled_memory_path = ROOT / "config" / "generated" / "memory_resource_models.json"
        bundled_memory_registry, bundled_memory_validation = _validated_memory_registry(
            bundled_memory_path
        )
        if bundled_memory_validation.get("valid"):
            memory_fallback_registry = bundled_memory_registry
            memory_fallback_validation = bundled_memory_validation
            memory_fallback_source = str(bundled_memory_path)

    for key, src in source_paths.items():
        if key == "memory":
            # Never seed or overwrite a Memory registry with generated_empty.
            if memory_fallback_registry:
                _write_json(registry_paths[key], memory_fallback_registry)
            continue
        data = _read_json(src)
        if data:
            _write_json(registry_paths[key], data)
        else:
            _write_json(registry_paths[key], {"version": "generated_empty", "models": {}})

    summary: Dict[str, Any] = {
        "run_id": run_id,
        "feature_cache_dir": str(feature_cache_dir),
        "data_dir": str(data_dir),
        "output_dir": _relative_to_root(output_dir, root),
        "paths": {
            "cpu_json": _relative_to_root(registry_paths["cpu"], root),
            "io_json": _relative_to_root(registry_paths["io"], root),
            "memory_json": _relative_to_root(registry_paths["memory"], root),
        },
        "resources": {},
        "errors": {},
    }

    try:
        summary["resources"]["cpu"] = _export_cpu_artifacts(
            registry_path=registry_paths["cpu"],
            data_dir=data_dir,
            artifact_dir=artifact_dir,
            seed=seed,
        )
    except Exception as exc:
        summary["errors"]["cpu"] = str(exc)

    try:
        summary["resources"]["io"] = _export_io_artifacts(
            registry_path=registry_paths["io"],
            data_dir=data_dir,
            artifact_dir=artifact_dir,
            seed=seed,
        )
    except Exception as exc:
        summary["errors"]["io"] = str(exc)

    try:
        summary["resources"]["memory"] = _export_memory_artifacts(
            registry_path=registry_paths["memory"],
            data_dir=data_dir,
            artifact_dir=artifact_dir,
            root=root,
            seed=seed,
        )
    except Exception as exc:
        error = str(exc)
        if (
            "insufficient" in error.lower()
            and memory_fallback_registry
            and memory_fallback_validation.get("valid")
        ):
            # The frozen model remains the estimator when this run has no
            # sufficient Memory train/test sample set.  Keep the skip explicit
            # and never leave an empty registry for the optimizer to consume.
            _write_json(registry_paths["memory"], memory_fallback_registry)
            summary["resources"]["memory"] = {
                "status": "retrain_skipped_insufficient_samples",
                "source": "existing_frozen_memory_registry",
                "source_path": memory_fallback_source,
                "path": _relative_to_root(registry_paths["memory"], root),
                "validation": memory_fallback_validation,
                "error": error,
            }
        else:
            summary["errors"]["memory"] = error

    _write_json(output_dir / "resource_model_retrain_summary.json", summary)
    return summary


def _extract_model_prediction(learned_model: Dict[str, Any], prediction_key: str) -> Tuple[float, float]:
    # Baseline anchoring must always consume the raw model prediction.  Using
    # prediction_before_error_bound here can feed an already anchored value
    # back into the same calibration and multiply delta_scale twice.
    raw_pred = _safe_float(
        learned_model.get("prediction_before_anchor_calibration"),
        _safe_float(
            learned_model.get("prediction_before_error_bound"),
            _safe_float(learned_model.get(prediction_key), 0.0),
        ),
    )
    window_s = _safe_float(learned_model.get("prediction_window_seconds"), 5.0)
    return raw_pred, max(1e-6, window_s)


def _set_baseline_error_bound(
    model: Dict[str, Any],
    *,
    value: float,
    unit: str,
    actual_target: float,
    predicted_target: float,
    normalized_resource: str,
    anchor_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    old_bound = model.get("baseline_error_bound") or {}
    old_value = _safe_float(old_bound.get("value"), 0.0) if isinstance(old_bound, dict) else 0.0
    bound_value = max(old_value, max(0.0, value))
    record = _error_bound_record(
        value=bound_value,
        unit=unit,
        source="baseline_actual_abs_error",
        sample_count=1,
        extra={
            "actual_target": float(actual_target),
            "predicted_target_before_error_bound": float(predicted_target),
            "normalized_resource": normalized_resource,
        },
    )
    model["baseline_error_bound"] = record
    scale = 1.0
    if abs(float(predicted_target)) > 1e-12:
        scale = float(actual_target) / float(predicted_target)
    clamped_scale = min(100.0, max(0.01, scale))
    anchor_record = {
        "source": "baseline_actual_plan_delta_anchor",
        "mode": "actual_baseline_plus_scaled_plan_delta",
        "unit": unit,
        "actual_target": float(actual_target),
        "predicted_target_before_error_bound": float(predicted_target),
        "delta_scale": clamped_scale,
        "unclamped_delta_scale": scale,
        "normalized_resource": normalized_resource,
        "sample_count": 1,
        "prediction_input_stage": "raw_model_before_anchor_and_error_bound",
        "prediction_output_stage": "after_baseline_anchor_before_error_bound",
        "anchor_application_count": 1,
        "notes": (
            "Point prediction is anchored to the first real baseline. Candidate deltas are still driven "
            "only by plan-tree feature changes."
        ),
    }
    if isinstance(anchor_metadata, dict):
        anchor_record.update(copy.deepcopy(anchor_metadata))
    model["baseline_anchor_calibration"] = anchor_record
    return record


def apply_baseline_error_bounds(
    *,
    artifact_paths: Dict[str, Any],
    baseline_actual_normalized: Dict[str, float],
    baseline_estimation_metadata: Dict[str, Any],
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = root or ROOT
    resource_breakdown = baseline_estimation_metadata.get("resource_breakdown") or {}
    summary: Dict[str, Any] = {"updated": {}, "skipped": {}, "paths": copy.deepcopy(artifact_paths)}

    cpu_path = _resolve_path(artifact_paths.get("cpu_json"), root)
    cpu_registry = _read_json(cpu_path)
    cpu_learned = (((resource_breakdown.get("cpu") or {}).get("calibration_breakdown") or {}).get("learned_model") or {})
    if cpu_registry and cpu_learned and "cpu" in baseline_actual_normalized:
        model_set = str(cpu_learned.get("model_set") or cpu_registry.get("default_model_set") or "C_only")
        model = ((cpu_registry.get("models") or {}).get(model_set) or {})
        pred, window_s = _extract_model_prediction(cpu_learned, "prediction_cpu_seconds_per_window")
        actual_target = _safe_float(baseline_actual_normalized.get("cpu"), 0.0) * window_s
        if model:
            record = _set_baseline_error_bound(
                model,
                value=abs(actual_target - pred),
                unit="cpu_seconds_per_window",
                actual_target=actual_target,
                predicted_target=pred,
                normalized_resource="cpu",
            )
            _write_json(cpu_path, cpu_registry)
            summary["updated"]["cpu"] = {"model_set": model_set, "baseline_error_bound": record}
        else:
            summary["skipped"]["cpu"] = "model_set_missing"
    else:
        summary["skipped"]["cpu"] = "missing_cpu_actual_or_prediction"

    memory_path = _resolve_path(artifact_paths.get("memory_json"), root)
    memory_registry = _read_json(memory_path)
    memory_learned = (((resource_breakdown.get("memory") or {}).get("calibration_breakdown") or {}).get("learned_model") or {})
    if memory_registry and memory_learned and "memory" in baseline_actual_normalized:
        model_set = str(memory_learned.get("model_set") or memory_registry.get("default_model_set") or "TP_Hybrid")
        model = ((memory_registry.get("models") or {}).get(model_set) or {})
        pred, _ = _extract_model_prediction(memory_learned, "prediction_memory_mb")
        prediction_source = "current_estimation_raw_before_anchor"
        stale_pre_mutation_prediction: Optional[float] = None
        baseline_training = model.get("baseline_training")
        if isinstance(baseline_training, dict):
            training_actual = _safe_float(baseline_training.get("actual_memory_mb"), 0.0)
            current_model_prediction = _safe_float(
                baseline_training.get(
                    "predicted_after_retrain_before_error_bound_mb"
                ),
                0.0,
            )
            actual_target_for_training = (
                _safe_float(baseline_actual_normalized.get("memory"), 0.0)
                * 1024.0
            )
            if (
                baseline_training.get("source") == "current_baseline_actual"
                and current_model_prediction > 0.0
                and math.isclose(
                    training_actual,
                    actual_target_for_training,
                    rel_tol=1e-12,
                    abs_tol=1e-9,
                )
            ):
                stale_pre_mutation_prediction = pred
                pred = current_model_prediction
                prediction_source = (
                    "current_model_baseline_training_after_retrain"
                )
        actual_target = _safe_float(baseline_actual_normalized.get("memory"), 0.0) * 1024.0
        memory_breakdown = resource_breakdown.get("memory") or {}
        memory_concurrency = memory_breakdown.get("concurrency_breakdown") or {}
        formula_target = _safe_float(
            memory_concurrency.get("concurrent_memory_bytes"),
            0.0,
        ) / (1024.0 ** 2)
        formula_scale = actual_target / formula_target if formula_target > 1e-12 else 1.0
        if model:
            record = _set_baseline_error_bound(
                model,
                value=abs(actual_target - pred),
                unit="MB",
                actual_target=actual_target,
                predicted_target=pred,
                normalized_resource="memory",
                anchor_metadata={
                    "raw_prediction_source": prediction_source,
                    "pre_model_mutation_prediction_before_anchor": (
                        stale_pre_mutation_prediction
                    ),
                    "plan_formula_predicted_target_before_error_bound": formula_target,
                    "plan_formula_delta_scale": formula_scale,
                },
            )
            _write_json(memory_path, memory_registry)
            summary["updated"]["memory"] = {"model_set": model_set, "baseline_error_bound": record}
        else:
            summary["skipped"]["memory"] = "model_set_missing"
    else:
        summary["skipped"]["memory"] = "missing_memory_actual_or_prediction"

    io_path = _resolve_path(artifact_paths.get("io_json"), root)
    io_registry = _read_json(io_path)
    io_updates: Dict[str, Any] = {}
    io_target_by_resource = {"io": "total", "io_read": "read", "io_write": "write"}
    for resource_name, target_name in io_target_by_resource.items():
        learned = (((resource_breakdown.get(resource_name) or {}).get("calibration_breakdown") or {}).get("learned_model") or {})
        if not io_registry or not learned or resource_name not in baseline_actual_normalized:
            continue
        model_set = str(learned.get("model_set") or io_registry.get("default_model_set") or "CH")
        model = ((((io_registry.get("models") or {}).get(model_set) or {}).get("targets") or {}).get(target_name) or {})
        pred, window_s = _extract_model_prediction(learned, "prediction_mb_per_window")
        actual_target = _safe_float(baseline_actual_normalized.get(resource_name), 0.0) * window_s
        breakdown = resource_breakdown.get(resource_name) or {}
        calibration = breakdown.get("calibration_breakdown") or {}
        execution = breakdown.get("execution_breakdown") or {}
        workload_context = execution.get("workload_context") or {}
        elapsed_seconds = max(
            1e-9,
            _safe_float(workload_context.get("elapsed_seconds"), window_s),
        )
        formula_bytes_key = {
            "io": "total_io_bytes",
            "io_read": "total_read_bytes",
            "io_write": "total_write_bytes",
        }[resource_name]
        formula_total_bytes = _safe_float(calibration.get(formula_bytes_key), 0.0)
        formula_target = (
            formula_total_bytes / elapsed_seconds * window_s / (1024.0 ** 2)
        )
        formula_scale = (
            actual_target / formula_target
            if formula_target > 1e-12
            else 1.0
        )
        if not model:
            continue
        record = _set_baseline_error_bound(
            model,
            value=abs(actual_target - pred),
            unit="MB/window",
            actual_target=actual_target,
            predicted_target=pred,
            normalized_resource=resource_name,
            anchor_metadata={
                "plan_formula_predicted_target_before_error_bound": formula_target,
                "plan_formula_delta_scale": formula_scale,
                "plan_formula_elapsed_seconds": elapsed_seconds,
                "plan_formula_total_bytes": formula_total_bytes,
            },
        )
        io_updates[resource_name] = {
            "model_set": model_set,
            "target": target_name,
            "baseline_error_bound": record,
        }
    if io_updates:
        _write_json(io_path, io_registry)
        summary["updated"]["io"] = io_updates
    else:
        summary["skipped"]["io"] = "missing_io_actual_or_prediction"

    return summary


def main() -> None:
    result = retrain_resource_models()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
