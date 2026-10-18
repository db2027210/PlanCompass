#!/usr/bin/env python3
"""Train JOB-only resource models with grouped leave-one-vector-out evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np

from plan_resource_estimator import PlanResourceEstimator
from parameter_optimizer import validate_resource_model_quality_gate


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
OUTER_ROOT = LANE_ROOT / "history/job/outer/attempt_1"
CALIBRATION_ROOT = OUTER_ROOT / "job_quality_calibration"
FIRST_SELECTION_PATH = CALIBRATION_ROOT / "calibration_selection.json"
FIRST_QUEUE_STATE_PATH = CALIBRATION_ROOT / "measurement_queue_state.json"
SECOND_BATCH_ROOT = CALIBRATION_ROOT / "response_span_batch2"
SECOND_SELECTION_PATH = SECOND_BATCH_ROOT / "response_span_selection.json"
SECOND_QUEUE_STATE_PATH = SECOND_BATCH_ROOT / "measurement_queue_state.json"
CONFIG_PATH = (
    OUTER_ROOT
    / "generated_config/formal_job_outer_attempt1/memory/job.json"
)
OUTPUT_ROOT = CALIBRATION_ROOT / "job_resource_model_bundle_v3_role_aware"
POLICY_PATH = LANE_ROOT / "launch_outer/job_role_aware_r2_fallback_policy.json"
BASELINE_ORDINAL = 4
EXPECTED_SAMPLE_COUNT = 9
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
EXPECTED_MEMORY_BYTES = 3124756480
EXPECTED_CPU_CORES = 3.0
EXPECTED_IO_BPS = 134217728
EXPECTED_TERMINALS = 2
MODEL_WINDOW_SECONDS = 5.0
MODEL_FAMILIES = (
    "formula_ratio",
    "formula_affine",
    "ridge_formula_cost_alpha_0.1",
    "ridge_formula_cost_alpha_1",
    "ridge_formula_cost_alpha_10",
)


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


def signature(params: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {name: float(params[name]).hex() for name in ACTIVE_PARAMS},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def formula_only_estimator_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(((cfg.get("metrics") or {}).get("plan_estimator") or {}))
    result.pop("model_retraining", None)
    result.setdefault("shared", {})["explain_workers"] = 1
    for resource in ("cpu", "io", "memory"):
        learned = result.setdefault(resource, {}).setdefault("learned_model", {})
        learned["enabled"] = False
        learned["required"] = False
    return result


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(item) for item in values)
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def metrics(actual: Sequence[float], predicted: Sequence[float]) -> Dict[str, Any]:
    y = np.asarray(actual, dtype=float)
    p = np.asarray(predicted, dtype=float)
    errors = np.abs(y - p)
    relative = errors / np.maximum(np.abs(y), 1e-9)
    denominator = float(np.sum((y - float(np.mean(y))) ** 2))
    target_mean = float(np.mean(y))
    target_std = float(np.std(y))
    target_min = float(np.min(y))
    target_max = float(np.max(y))
    target_range = target_max - target_min
    mean_denominator = max(abs(target_mean), 1e-18)
    r2 = 0.0 if denominator <= 1e-18 else 1.0 - float(np.sum((y - p) ** 2)) / denominator
    return {
        "rmse": float(np.sqrt(np.mean((y - p) ** 2))),
        "mae": float(np.mean(errors)),
        "r2": r2,
        "abs_error_p95": percentile(errors.tolist(), 0.95),
        "abs_error_p90": percentile(errors.tolist(), 0.90),
        "abs_error_p50": percentile(errors.tolist(), 0.50),
        "abs_error_max": float(np.max(errors)),
        "relative_error_p95": percentile(relative.tolist(), 0.95),
        "relative_error_p90": percentile(relative.tolist(), 0.90),
        "relative_error_p50": percentile(relative.tolist(), 0.50),
        "relative_error_max": float(np.max(relative)),
        "sample_count": int(len(y)),
        "label_distribution": {
            "mean": target_mean,
            "population_std": target_std,
            "min": target_min,
            "max": target_max,
            "range": target_range,
            "coefficient_of_variation": target_std / mean_denominator,
            "range_over_abs_mean": target_range / mean_denominator,
        },
    }


def fit_family(
    family: str,
    x: np.ndarray,
    y: np.ndarray,
    formula_feature: str,
    feature_names: Sequence[str],
) -> Dict[str, Any]:
    formula_index = feature_names.index(formula_feature)
    formula = x[:, formula_index]
    if family == "formula_ratio":
        denominator = float(np.dot(formula, formula))
        coefficient = float(np.dot(formula, y) / denominator) if denominator > 1e-18 else 0.0
        return {"features": [formula_feature], "coef": [coefficient], "intercept": 0.0}
    if family == "formula_affine":
        design = np.column_stack([np.ones(len(formula)), formula])
        solution = np.linalg.lstsq(design, y, rcond=None)[0]
        return {
            "features": [formula_feature],
            "coef": [float(solution[1])],
            "intercept": float(solution[0]),
        }
    alpha = float(family.rsplit("_", 1)[-1])
    mean = np.mean(x, axis=0)
    scale = np.std(x, axis=0)
    scale = np.where(scale < 1e-12, 1.0, scale)
    z = (x - mean) / scale
    centered_y = y - float(np.mean(y))
    coefficient_z = np.linalg.solve(
        z.T @ z + alpha * np.eye(z.shape[1]),
        z.T @ centered_y,
    )
    coefficient_raw = coefficient_z / scale
    intercept = float(np.mean(y) - np.dot(coefficient_raw, mean))
    return {
        "features": list(feature_names),
        "coef": [float(value) for value in coefficient_raw],
        "intercept": intercept,
    }


def predict(model: Mapping[str, Any], row: Mapping[str, float]) -> float:
    value = float(model.get("intercept") or 0.0)
    for name, coefficient in zip(model.get("features") or [], model.get("coef") or []):
        value += float(coefficient) * float(row.get(str(name), 0.0))
    return max(1e-9, value)


def select_family_by_lovo(
    indices: Sequence[int],
    rows: Sequence[Mapping[str, float]],
    targets: Sequence[float],
    formula_feature: str,
    feature_names: Sequence[str],
) -> Tuple[str, Dict[str, float]]:
    scores: Dict[str, float] = {}
    for family in MODEL_FAMILIES:
        predictions: List[float] = []
        actual: List[float] = []
        for held_out in indices:
            train = [index for index in indices if index != held_out]
            x_train = np.asarray(
                [[rows[index][name] for name in feature_names] for index in train],
                dtype=float,
            )
            y_train = np.asarray([targets[index] for index in train], dtype=float)
            model = fit_family(family, x_train, y_train, formula_feature, feature_names)
            predictions.append(predict(model, rows[held_out]))
            actual.append(float(targets[held_out]))
        scores[family] = float(np.mean((np.asarray(actual) - np.asarray(predictions)) ** 2))
    selected = min(MODEL_FAMILIES, key=lambda name: (scores[name], MODEL_FAMILIES.index(name)))
    return selected, scores


def train_resource(
    rows: Sequence[Mapping[str, float]],
    targets: Sequence[float],
    formula_feature: str,
) -> Dict[str, Any]:
    feature_names = [
        formula_feature,
        *(f"log_cost_param_{name}" for name in ACTIVE_PARAMS),
    ]
    indices = list(range(len(rows)))
    outer_predictions: List[float] = []
    outer_families: List[str] = []
    for held_out in indices:
        train = [index for index in indices if index != held_out]
        family, _ = select_family_by_lovo(
            train, rows, targets, formula_feature, feature_names
        )
        x_train = np.asarray(
            [[rows[index][name] for name in feature_names] for index in train],
            dtype=float,
        )
        y_train = np.asarray([targets[index] for index in train], dtype=float)
        model = fit_family(family, x_train, y_train, formula_feature, feature_names)
        outer_predictions.append(predict(model, rows[held_out]))
        outer_families.append(family)

    selected_family, family_scores = select_family_by_lovo(
        indices, rows, targets, formula_feature, feature_names
    )
    x_all = np.asarray(
        [[row[name] for name in feature_names] for row in rows], dtype=float
    )
    y_all = np.asarray(targets, dtype=float)
    final_model = fit_family(
        selected_family, x_all, y_all, formula_feature, feature_names
    )
    fitted = [predict(final_model, row) for row in rows]
    quality = {
        "train": metrics(targets, fitted),
        "test": metrics(targets, outer_predictions),
        "train_samples": len(rows),
        "test_samples": len(rows),
        "split_strategy": "grouped_leave_one_unique_parameter_vector_out",
        "group_key": "exact_float_hex_parameter_vector_sha256",
        "outer_fold_count": len(rows),
        "nested_model_selection": True,
        "predeclared_model_families": list(MODEL_FAMILIES),
        "outer_selected_families": outer_families,
        "full_data_selected_family": selected_family,
        "full_data_family_lovo_mse": family_scores,
        "oof_predictions": [float(value) for value in outer_predictions],
        "actual": [float(value) for value in targets],
    }
    final_model.update(
        {
            "model_type": "linear",
            "clip_min": 1e-9,
            "benchmark_scoped_absolute_calibration": True,
            "metrics": quality,
            "model_selection": {
                "selected_family": selected_family,
                "nested_lovo_evaluation": True,
                "families": list(MODEL_FAMILIES),
            },
        }
    )
    return final_model


def load_samples() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    selections = [read_json(FIRST_SELECTION_PATH), read_json(SECOND_SELECTION_PATH)]
    queue_states = [read_json(FIRST_QUEUE_STATE_PATH), read_json(SECOND_QUEUE_STATE_PATH)]
    incomplete = [state.get("status") for state in queue_states if state.get("status") != "complete"]
    if incomplete:
        raise RuntimeError(f"calibration queues are not complete: {incomplete}")
    second_selection_sha = sha256(SECOND_SELECTION_PATH)
    if queue_states[1].get("selection_sha256") != second_selection_sha:
        raise RuntimeError("second queue is not bound to the response-span selection SHA")
    ordinals = [BASELINE_ORDINAL] + [
        int(item["ordinal"])
        for queue_state in queue_states
        for item in queue_state.get("valid_points") or []
    ]
    if len(ordinals) != EXPECTED_SAMPLE_COUNT:
        raise RuntimeError(
            f"expected baseline plus eight valid vectors, got {ordinals}"
        )
    samples: List[Dict[str, Any]] = []
    signatures = set()
    for ordinal in ordinals:
        point_dir = OUTER_ROOT / "formal_points" / f"point_{ordinal:04d}"
        result_path = point_dir / "point_result.json"
        result = read_json(result_path)
        validity_path = point_dir / "formal_replay_validity.json"
        validity = read_json(validity_path)
        if ordinal != BASELINE_ORDINAL and (
            validity.get("eligible_for_benchmark_scoped_training") is not True
            or validity.get("independent_parameter_vector_sample") is not True
        ):
            raise RuntimeError(f"point {ordinal} is not eligible independent training data")
        params = result.get("cost_parameters") or {}
        vector_signature = signature(params)
        if vector_signature in signatures:
            raise RuntimeError(f"duplicate parameter vector at point {ordinal}")
        signatures.add(vector_signature)
        measurement = result.get("measurement") or {}
        means = measurement.get("resource_mean_utilization") or {}
        requested = (result.get("readback") or {}).get("requested_resource_profile") or {}
        point_checks = {
            "status_complete": result.get("status") == "complete",
            "correctness": measurement.get("correctness_passed") is True,
            "zero_error_rate": float(measurement.get("error_rate") or 0.0) == 0.0,
            "stable": (measurement.get("resource_stability") or {}).get("passed") is True,
            "no_oom": result.get("oom_killed") is False,
            "no_restart": int(result.get("database_restart_count") or 0) == 0,
            "memory_cap": int(requested.get("memory_bytes") or 0) == EXPECTED_MEMORY_BYTES,
            "cpu_cap": float(requested.get("cpu_quota_cores") or 0.0)
            == EXPECTED_CPU_CORES,
            "read_cap": int(requested.get("io_read_bps") or 0) == EXPECTED_IO_BPS,
            "write_cap": int(requested.get("io_write_bps") or 0) == EXPECTED_IO_BPS,
            "terminals": int((result.get("warmup") or {}).get("configured_terminals") or 0)
            == EXPECTED_TERMINALS,
        }
        if not all(point_checks.values()):
            raise RuntimeError(
                f"point {ordinal} failed training-data validity checks: {point_checks}"
            )
        profile_checks = {
            "cpu_30_50": 0.30 <= float(means["cpu"]) <= 0.50,
            "memory_85_95": 0.85 <= float(means["memory"]) <= 0.95,
            "io_30_50": 0.30 <= float(means["io"]) <= 0.50,
        }
        samples.append(
            {
                "ordinal": ordinal,
                "point_result_path": str(result_path),
                "point_result_sha256": sha256(result_path),
                "validity_path": str(validity_path),
                "validity_sha256": sha256(validity_path),
                "params": {name: float(params[name]) for name in ACTIVE_PARAMS},
                "vector_signature": vector_signature,
                "resource_mean_utilization": means,
                "profile_checks": profile_checks,
                "point_validity_checks": point_checks,
                "actual": {
                    "cpu_cores": float(means["cpu"]) * 3.0,
                    "memory_mb": float(means["memory"]) * EXPECTED_MEMORY_BYTES / (1024.0 ** 2),
                    "io_mb_s": float(means["io"]) * 256.0,
                },
            }
        )
    failed_profiles = [
        {"ordinal": item["ordinal"], "checks": item["profile_checks"]}
        for item in samples
        if not all(item["profile_checks"].values())
    ]
    if failed_profiles:
        raise RuntimeError(f"JOB quality profile check failed: {failed_profiles}")
    return samples, selections


def main() -> int:
    os.environ.setdefault("PGPASSWORD", "CHANGE_ME")
    if OUTPUT_ROOT.exists() or OUTPUT_ROOT.is_symlink():
        raise RuntimeError(f"immutable v3 output already exists: {OUTPUT_ROOT}")
    staging_root = OUTPUT_ROOT.with_name(f"{OUTPUT_ROOT.name}.tmp.{os.getpid()}")
    if staging_root.exists() or staging_root.is_symlink():
        raise RuntimeError(f"v3 staging output already exists: {staging_root}")
    cfg = read_json(CONFIG_PATH)
    policy = read_json(POLICY_PATH)
    if (
        policy.get("policy_id") != "pg5_job_role_aware_r2_fallback_v1"
        or policy.get("campaign_id") != "pg5_common_outer_bgallowed_20260722_v1"
        or policy.get("workload") != "job"
        or policy.get("target_resource") != "memory"
    ):
        raise RuntimeError("unexpected JOB role-aware R2 fallback policy scope")
    samples, selections = load_samples()
    baseline_result = read_json(
        OUTER_ROOT / f"formal_points/point_{BASELINE_ORDINAL:04d}/point_result.json"
    )
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
    context = {
        "workload_family": "H",
        "benchmark_name": "job",
        "elapsed_seconds": 180.0,
        "feature_window_seconds": MODEL_WINDOW_SECONDS,
        "concurrency": {"avg": 2.0},
        "active_sql_count": 2.0,
        "cache_hit_rate": 0.976836989624045,
        "baseline_runtime_memory_bytes": samples[0]["actual"]["memory_mb"] * 1024.0 ** 2,
    }
    feature_rows: List[Dict[str, float]] = []
    for sample in samples:
        resource, _, metadata = estimator.estimate_resources_for_queries(
            queries,
            params=sample["params"],
            workload_context=context,
        )
        row = {
            "job_plan_formula_cpu_seconds_per_window": float(resource["cpu"])
            * MODEL_WINDOW_SECONDS,
            "job_plan_formula_memory_mb": float(resource["memory"]) * 1024.0,
            "job_plan_formula_io_total_mb_per_window": float(resource["io"])
            * MODEL_WINDOW_SECONDS,
        }
        row.update(
            {
                f"cost_param_{name}": float(sample["params"][name])
                for name in ACTIVE_PARAMS
            }
        )
        row.update(
            {
                f"log_cost_param_{name}": math.log(float(sample["params"][name]))
                for name in ACTIVE_PARAMS
            }
        )
        sample["formula_features"] = row
        sample["formula_estimation_metadata_sha256"] = hashlib.sha256(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        feature_rows.append(row)

    targets = {
        "cpu": [sample["actual"]["cpu_cores"] * MODEL_WINDOW_SECONDS for sample in samples],
        "memory": [sample["actual"]["memory_mb"] for sample in samples],
        "io": [sample["actual"]["io_mb_s"] * MODEL_WINDOW_SECONDS for sample in samples],
    }
    cpu_model = train_resource(
        feature_rows, targets["cpu"], "job_plan_formula_cpu_seconds_per_window"
    )
    memory_model = train_resource(
        feature_rows, targets["memory"], "job_plan_formula_memory_mb"
    )
    io_model = train_resource(
        feature_rows, targets["io"], "job_plan_formula_io_total_mb_per_window"
    )
    for model, unit, target in (
        (cpu_model, "cpu_seconds_per_5s_window", "actual_cpu_seconds_per_window"),
        (memory_model, "MB", "actual_memory_mb"),
        (io_model, "MB_per_5s_window", "actual_total_io_mb_per_window"),
    ):
        model["target"] = target
        model["target_unit"] = unit
        model["prediction_unit"] = unit
        model["error_bound"] = {
            "value": max(
                abs(actual - predicted)
                for actual, predicted in zip(
                    model["metrics"]["actual"],
                    model["metrics"]["oof_predictions"],
                )
            ),
            "sample_count": len(samples),
            "source": "grouped_lovo_oof_max_abs_error",
            "unit": unit,
        }
    utilization_denominators = {
        "cpu": 3.0 * MODEL_WINDOW_SECONDS,
        "io": 256.0 * MODEL_WINDOW_SECONDS,
        "memory": EXPECTED_MEMORY_BYTES / (1024.0 ** 2),
    }
    for resource, model in (
        ("cpu", cpu_model),
        ("io", io_model),
        ("memory", memory_model),
    ):
        denominator = utilization_denominators[resource]
        for split in ("train", "test"):
            split_metrics = model["metrics"][split]
            split_metrics["abs_error_max_utilization_pp"] = (
                100.0 * float(split_metrics["abs_error_max"]) / denominator
            )
            split_metrics["abs_error_p95_utilization_pp"] = (
                100.0 * float(split_metrics["abs_error_p95"]) / denominator
            )

    cpu_registry = {
        "version": "job_quality_grouped_lovo_v3_role_aware",
        "default_model_set": "JOB",
        "models": {"JOB": cpu_model},
    }
    io_registry = {
        "version": "job_quality_grouped_lovo_v3_role_aware",
        "default_model_set": "JOB",
        "models": {
            "JOB": {
                "notes": "JOB-only unique-vector grouped LOVO model",
                "targets": {"total": io_model},
            }
        },
    }
    memory_registry = {
        "version": "job_quality_grouped_lovo_v3_role_aware",
        "default_model_set": "JOB",
        "models": {"JOB": memory_model},
    }
    dataset = {
        "schema_version": 1,
        "workload": "JOB",
        "sample_count": len(samples),
        "unique_parameter_vector_count": len({item["vector_signature"] for item in samples}),
        "grouping": "exact_float_hex_parameter_vector_sha256",
        "selection_sources": [
            {
                "path": str(path),
                "sha256": sha256(path),
                "selection_id": selection.get("selection_id"),
            }
            for path, selection in zip(
                (FIRST_SELECTION_PATH, SECOND_SELECTION_PATH), selections
            )
        ],
        "baseline_query_counts_frozen_for_all_vectors": observed_counts,
        "samples": samples,
    }
    write_json(staging_root / "job_unique_vector_dataset.json", dataset)
    write_json(staging_root / "cpu_resource_models.json", cpu_registry)
    write_json(staging_root / "io_resource_models.json", io_registry)
    write_json(staging_root / "memory_resource_models.json", memory_registry)
    summary = {
        "run_id": "job_quality_grouped_lovo_v3_role_aware",
        "source": (
            "job_fresh_default_plus_first_plan_maximin_batch_plus_"
            "second_plan_response_span_batch"
        ),
        "dataset": {
            "path": str(OUTPUT_ROOT / "job_unique_vector_dataset.json"),
            "sample_count": len(samples),
            "unique_parameter_vector_count": len(samples),
            "grouped_lovo": True,
        },
        "resources": {
            "cpu": {"JOB": cpu_model["metrics"]},
            "io": {"JOB": {"total": io_model["metrics"]}},
            "memory": {
                "status": "retrained",
                "source": "job_grouped_lovo_unique_parameter_vectors",
                "actual_memory_mb": samples[0]["actual"]["memory_mb"],
                "selected_model_set": "JOB",
                "updated_model_sets": {
                    "JOB": {
                        "model_type": memory_model["model_type"],
                        "metrics": memory_model["metrics"],
                        "error_bound": memory_model["error_bound"],
                    }
                },
            },
        },
        "errors": {},
        "quality_gate_context": {
            "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
            "workload": "job",
            "target_resource": "memory",
            "unique_parameter_vector_count": len(samples),
            "grouping": "exact_float_hex_parameter_vector_sha256",
            "policy_id": policy["policy_id"],
            "policy_source_path": str(POLICY_PATH),
            "policy_source_sha256": sha256(POLICY_PATH),
        },
    }
    write_json(staging_root / "resource_model_retrain_summary.json", summary)
    runtime_selection_metadata = {
        "resource_breakdown": {
            resource: {
                "calibration_breakdown": {
                    "learned_model": {"model_set": "JOB"}
                }
            }
            for resource in ("cpu", "io", "memory")
        }
    }
    base_quality_gate_cfg = copy.deepcopy((((cfg.get("metrics") or {}).get("plan_estimator") or {}).get(
        "model_retraining"
    ) or {}).get("quality_gate") or {})
    quality_gate_cfg = copy.deepcopy(base_quality_gate_cfg)
    quality_gate_cfg["target_resource"] = "memory"
    quality_gate_cfg["r2_fallback"] = copy.deepcopy(policy)
    exact_gate = validate_resource_model_quality_gate(
        summary,
        quality_gate_cfg,
        baseline_estimation_metadata=runtime_selection_metadata,
    )
    quality = {
        "cpu_test": cpu_model["metrics"]["test"],
        "io_test": io_model["metrics"]["test"],
        "memory_test": memory_model["metrics"]["test"],
        "thresholds": quality_gate_cfg,
        "base_quality_gate_from_generated_config": base_quality_gate_cfg,
        "base_min_test_r2_unchanged": (
            float(base_quality_gate_cfg.get("min_test_r2")) == 0.0
            and float(quality_gate_cfg.get("min_test_r2")) == 0.0
        ),
        "target_resource_unchanged": quality_gate_cfg.get("target_resource") == "memory",
        "r2_fallback_policy": policy,
        "exact_parameter_optimizer_quality_gate": exact_gate,
        "passed": exact_gate.get("passed") is True,
    }
    quality_path = staging_root / "grouped_lovo_quality.json"
    write_json(quality_path, quality)
    write_json(staging_root / "r2_fallback_policy.json", policy)
    bundle_files = [
        "job_unique_vector_dataset.json",
        "cpu_resource_models.json",
        "io_resource_models.json",
        "memory_resource_models.json",
        "resource_model_retrain_summary.json",
        "grouped_lovo_quality.json",
        "r2_fallback_policy.json",
    ]
    file_hashes = {
        name: sha256(staging_root / name)
        for name in bundle_files
    }
    bundle_content_sha256 = hashlib.sha256(
        json.dumps(file_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema_version": 1,
        "bundle_id": "job_quality_grouped_lovo_v3_role_aware",
        "r2_fallback_policy_sha256": sha256(
            staging_root / "r2_fallback_policy.json"
        ),
        "bundle_content_sha256": bundle_content_sha256,
        "files": file_hashes,
    }
    manifest_path = staging_root / "bundle_manifest.json"
    write_json(manifest_path, manifest)
    staging_root.replace(OUTPUT_ROOT)
    manifest_path = OUTPUT_ROOT / "bundle_manifest.json"
    print(
        json.dumps(
            {
                "quality": quality,
                "bundle_content_sha256": bundle_content_sha256,
                "bundle_manifest_sha256": sha256(manifest_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if quality["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
