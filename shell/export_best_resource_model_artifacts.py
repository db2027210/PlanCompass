#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split


ROOT = Path(__file__).resolve().parents[1]
FC_DIR = Path("/home/anonymous/program/features_cache_M1")
DATA_DIR = FC_DIR / "data"
OUT_DIR = ROOT / "config" / "generated" / "model_artifacts"
CPU_JSON = ROOT / "config" / "generated" / "cpu_resource_models.json"
IO_JSON = ROOT / "config" / "generated" / "io_resource_models.json"
SEED = 42

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


def evaluate(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    return {
        "rmse": float(math.sqrt(mean_squared_error(y_true, y_pred))),
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "r2": float(r2_score(y_true, y_pred)),
    }


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    return df.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def cpu_features(df: pd.DataFrame, include_active: bool) -> pd.DataFrame:
    cols = [c for c in CPU_BASE_FEATURES if c in df.columns]
    x = df[cols].astype(float).copy()
    if include_active:
        active = (
            df["active_sql_count"].astype(float).clip(lower=1.0)
            if "active_sql_count" in df.columns
            else pd.Series(np.ones(len(df)), index=df.index)
        )
        x["active_sql_count"] = active
        x["log_active_sql_count"] = np.log1p(active)
        for col in cols:
            x[f"{col}_per_active"] = x[col] / active
    return clean_df(x)


def fit_regressor(
    x: pd.DataFrame,
    y: np.ndarray,
    *,
    model_name: str,
    log_target: bool,
) -> Tuple[Any, Dict[str, Any]]:
    idx = np.arange(len(y))
    train_idx, test_idx = train_test_split(idx, test_size=0.2, random_state=SEED)
    x_train, x_test = x.iloc[train_idx], x.iloc[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]

    if model_name == "hist_gb":
        model = HistGradientBoostingRegressor(
            learning_rate=0.04,
            max_depth=8,
            max_iter=1200,
            min_samples_leaf=15,
            random_state=SEED,
        )
    elif model_name == "rf":
        model = RandomForestRegressor(
            n_estimators=700,
            max_depth=24,
            min_samples_leaf=2,
            random_state=SEED,
            n_jobs=-1,
        )
    else:
        model = ExtraTreesRegressor(
            n_estimators=700,
            max_depth=None,
            min_samples_leaf=4,
            random_state=SEED,
            n_jobs=-1,
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
        "train": evaluate(y_train, pred_train),
        "test": evaluate(y_test, pred_test),
        "train_samples": int(len(train_idx)),
        "test_samples": int(len(test_idx)),
    }
    return model, metrics


def save_artifact(
    *,
    filename: str,
    model: Any,
    features: List[str],
    model_type: str,
    target_transform: str | None,
    metrics: Dict[str, Any],
) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / filename
    payload = {
        "model": model,
        "features": features,
        "model_type": model_type,
        "target_transform": target_transform,
        "clip_min": 0.0,
        "metrics": metrics,
    }
    joblib.dump(payload, path)
    return str(path.relative_to(ROOT))


def load_cpu_dataset(pattern: str) -> pd.DataFrame:
    frames = []
    for path in sorted(DATA_DIR.glob(pattern)):
        df = pd.read_parquet(path)
        if "target_cpu" not in df.columns:
            continue
        df = df.copy()
        df["source_file"] = path.name
        frames.append(df)
    if not frames:
        raise RuntimeError(f"no CPU parquet files matched {pattern}")
    out = clean_df(pd.concat(frames, ignore_index=True))
    return out[out["target_cpu"] > 0].reset_index(drop=True)


def export_cpu_artifacts() -> Dict[str, Any]:
    registry = read_json(CPU_JSON)
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
                p for p in DATA_DIR.glob(pattern)
                if re.match(r"^c\d+_5s_features\.parquet$", p.name)
            ]
            df = clean_df(pd.concat([pd.read_parquet(p) for p in sorted(files)], ignore_index=True))
            df = df[df["target_cpu"] > 0].reset_index(drop=True)
        else:
            df = load_cpu_dataset(pattern)
        x = cpu_features(df, include_active=include_active)
        y = df["target_cpu"].astype(float).to_numpy()
        model, metrics = fit_regressor(
            x,
            y,
            model_name=model_name,
            log_target=(model_set != "H_serial"),
        )
        artifact_path = save_artifact(
            filename=f"cpu_{model_set}.joblib",
            model=model,
            features=list(x.columns),
            model_type=f"joblib_{model_name}",
            target_transform="log1p_expm1" if model_set != "H_serial" else None,
            metrics=metrics,
        )
        rec = models.setdefault(model_set, {})
        rec["artifact_path"] = artifact_path
        rec["model_type"] = f"joblib_{model_name}"
        rec["features"] = list(x.columns)
        rec["target_transform"] = "log1p_expm1" if model_set != "H_serial" else None
        rec["metrics"] = metrics
        summary[model_set] = metrics

    registry["artifact_export"] = {
        "script": str(Path(__file__).relative_to(ROOT)),
        "output_dir": str(OUT_DIR.relative_to(ROOT)),
    }
    write_json(CPU_JSON, registry)
    return summary


def complete_io_features(df: pd.DataFrame, features: List[str]) -> pd.DataFrame:
    x = pd.DataFrame(index=df.index)
    for col in features:
        x[col] = df[col].astype(float) if col in df.columns else 0.0
    return clean_df(x)


def load_io_dataset(family: str) -> pd.DataFrame:
    if family == "H":
        cpu_files = sorted(DATA_DIR.glob("h*_serial_features.parquet"))
        io_map = {p.name.split("_io_")[0]: p for p in DATA_DIR.glob("h*_io_serial_features.parquet")}
        frames = []
        for cpu_path in cpu_files:
            case = cpu_path.name.split("_serial_")[0]
            io_path = io_map.get(case)
            if io_path is None:
                continue
            cpu_df = pd.read_parquet(cpu_path)
            io_df = pd.read_parquet(io_path)
            dup = [c for c in io_df.columns if c in cpu_df.columns]
            frames.append(pd.concat([cpu_df, io_df.drop(columns=dup, errors="ignore")], axis=1))
        return clean_df(pd.concat(frames, ignore_index=True))
    if family == "C":
        files = [
            p for p in DATA_DIR.glob("c*_io_5s_features.parquet")
            if re.match(r"^c\d+_io_5s_features\.parquet$", p.name)
        ]
    else:
        files = [
            p for p in DATA_DIR.glob("ch*_io_5s_features.parquet")
            if re.match(r"^ch\d+_io_5s_features\.parquet$", p.name)
        ]
    return clean_df(pd.concat([pd.read_parquet(p) for p in sorted(files)], ignore_index=True))


def export_io_artifacts() -> Dict[str, Any]:
    registry = read_json(IO_JSON)
    models = registry.setdefault("models", {})
    target_map = {
        "total": "target_io_mb",
        "read": "target_io_read_mb",
        "write": "target_io_write_mb",
    }
    feature_union = sorted(
        set(IO_BASE_FEATURES)
        | {
            "io_pg_total_cost", "io_pg_run_cost", "io_tree_max_depth", "io_tree_node_count",
            "io_filter_ops", "io_ratio_npage_per_cost", "io_ratio_nipage_per_cost",
            "io_ratio_idx_vs_table", "io_ratio_outer_vs_inner_rows",
            "io_ratio_pages_per_row", "io_ratio_outer_vs_inner_pages",
            "io_ratio_run_vs_total_cost", "io_spill_pressure", "io_join_pressure",
            "io_index_pressure", "io_seq_scan_share", "io_bitmap_share", "io_cost_per_page",
        }
    )
    summary: Dict[str, Any] = {}
    for family in ["H", "C", "CH"]:
        df = load_io_dataset(family)
        group = models.setdefault(family, {}).setdefault("targets", {})
        summary[family] = {}
        for target_name, target_col in target_map.items():
            if target_col not in df.columns:
                continue
            cur = df[df[target_col] > 0].reset_index(drop=True)
            if len(cur) < 20:
                continue
            x = complete_io_features(cur, feature_union)
            y = cur[target_col].astype(float).to_numpy()
            model_name = "hist_gb"
            if family == "CH" and target_name in {"total", "write"}:
                model_name = "extra"
            if family == "CH" and target_name == "read":
                model_name = "rf"
            model, metrics = fit_regressor(x, y, model_name=model_name, log_target=False)
            artifact_path = save_artifact(
                filename=f"io_{family}_{target_name}.joblib",
                model=model,
                features=list(x.columns),
                model_type=f"joblib_{model_name}",
                target_transform=None,
                metrics=metrics,
            )
            rec = group.setdefault(target_name, {})
            rec["artifact_path"] = artifact_path
            rec["model_type"] = f"joblib_{model_name}"
            rec["features"] = list(x.columns)
            rec["target_transform"] = None
            rec["metrics"] = metrics
            summary[family][target_name] = metrics

    registry["artifact_export"] = {
        "script": str(Path(__file__).relative_to(ROOT)),
        "output_dir": str(OUT_DIR.relative_to(ROOT)),
    }
    write_json(IO_JSON, registry)
    return summary


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "cpu": export_cpu_artifacts(),
        "io": export_io_artifacts(),
    }
    summary_path = OUT_DIR / "artifact_export_summary.json"
    write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"summary: {summary_path}")


if __name__ == "__main__":
    main()
