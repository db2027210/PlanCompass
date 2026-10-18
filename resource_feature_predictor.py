from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


FeatureDict = Dict[str, float]
Prediction = Dict[str, Any]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    return default


def _resolve_path(root: Path, value: Any, default: str) -> Path:
    path = Path(str(value or default))
    if path.is_absolute():
        return path
    return root / path


class ResourceFeaturePredictor:
    """
    Unified feature-to-resource predictor.

    Input is already-built model features. SQL collection, EXPLAIN, plan parsing,
    and workload replay accounting are intentionally outside this class.
    """

    def __init__(
        self,
        *,
        root: Optional[Path] = None,
        estimator_cfg: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.root = root or Path.cwd()
        self.estimator_cfg = estimator_cfg or {}
        self.cpu_model_artifact = self._load_model_artifact(
            section="cpu",
            default_path="config/generated/cpu_resource_models.json",
        )
        self.io_model_artifact = self._load_model_artifact(
            section="io",
            default_path="config/generated/io_resource_models.json",
        )
        self.memory_model_artifact = self._load_model_artifact(
            section="memory",
            default_path="config/generated/memory_resource_models.json",
        )
        self._joblib_model_cache: Dict[str, Any] = {}

    def predict(self, feature_input: Dict[str, Any]) -> Dict[str, Prediction]:
        """
        Predict CPU, Memory, IO, IO read, and IO write from prepared features.

        Expected input shape:
        {
            "cpu_features": {...},
            "memory_features": {...},
            "io_features": {...},
            "workload_context": {...}
        }
        """
        workload_context = dict(feature_input.get("workload_context") or {})
        return {
            "cpu": self.predict_cpu(
                features=feature_input.get("cpu_features") or {},
                workload_context=workload_context,
            ),
            "memory": self.predict_memory(
                features=feature_input.get("memory_features") or {},
                workload_context=workload_context,
            ),
            "io": self.predict_io(
                target_name="total",
                features=feature_input.get("io_features") or {},
                workload_context=workload_context,
            ),
            "io_read": self.predict_io(
                target_name="read",
                features=feature_input.get("io_features") or {},
                workload_context=workload_context,
            ),
            "io_write": self.predict_io(
                target_name="write",
                features=feature_input.get("io_features") or {},
                workload_context=workload_context,
            ),
        }

    def predict_cpu(
        self,
        *,
        features: Dict[str, Any],
        workload_context: Dict[str, Any],
    ) -> Prediction:
        if not self.cpu_model_artifact:
            return {"available": False, "reason": "cpu_model_disabled_or_missing"}
        model_set = self._cpu_model_set_name(workload_context)
        model = ((self.cpu_model_artifact.get("models") or {}).get(model_set) or {})
        pred, breakdown = self._predict_model(
            model=model,
            features=features,
            prediction_key="prediction_cpu_seconds_per_window",
        )
        if pred is None:
            return {"available": False, "model_set": model_set, "reason": "cpu_prediction_failed"}
        prediction_window_s = self._prediction_window_seconds(workload_context)
        cpu_cores = pred / prediction_window_s
        breakdown.update(
            {
                "available": True,
                "resource": "cpu",
                "model_set": model_set,
                "metrics": model.get("metrics"),
                "prediction_cpu_seconds_per_window": pred,
                "prediction_window_seconds": prediction_window_s,
                "prediction_cpu_cores": cpu_cores,
                "target_unit": model.get("target_unit"),
                "prediction_unit": model.get("prediction_unit"),
            }
        )
        return breakdown

    def predict_memory(
        self,
        *,
        features: Dict[str, Any],
        workload_context: Dict[str, Any],
    ) -> Prediction:
        if not self.memory_model_artifact:
            return {"available": False, "reason": "memory_model_disabled_or_missing"}
        model_set = self._memory_model_set_name(workload_context)
        model = ((self.memory_model_artifact.get("models") or {}).get(model_set) or {})
        if not model:
            return {"available": False, "model_set": model_set, "reason": "memory_model_set_missing"}

        artifact = self._load_joblib_artifact(model)
        if artifact:
            pred, breakdown = self._predict_joblib_artifact(
                artifact=artifact,
                model=model,
                features=features,
                prediction_key="prediction_memory_mb",
            )
            if pred is not None:
                breakdown.update(
                    {
                        "available": True,
                        "resource": "memory",
                        "model_set": model_set,
                        "prediction_memory_mb": pred,
                        "prediction_memory_bytes": pred * (1024.0 ** 2),
                        "metrics": model.get("metrics"),
                        "notes": model.get("notes"),
                    }
                )
                return breakdown

        model_type = model.get("model_type")
        pred = _safe_float(model.get("intercept"), 0.0)
        contributions: List[Dict[str, float]] = []
        model_features = list(model.get("features") or [])
        model_coef = list(model.get("coef") or [])
        if model_type != "constant":
            if not model_features or len(model_features) != len(model_coef):
                return {"available": False, "model_set": model_set, "reason": "memory_model_invalid"}
            for idx, feature in enumerate(model_features):
                raw = _safe_float(features.get(feature), 0.0)
                contribution = _safe_float(model_coef[idx], 0.0) * raw
                pred += contribution
                contributions.append(
                    {
                        "feature": feature,
                        "raw": raw,
                        "contribution": contribution,
                    }
                )
        clip_min = model.get("clip_min")
        if clip_min not in (None, ""):
            pred = max(_safe_float(clip_min), pred)
        pred_before_anchor_calibration = pred
        pred, anchor_calibration = self._apply_baseline_anchor_calibration(model=model, prediction=pred)
        pred_before_error_bound = pred
        error_bound = self._prediction_error_bound(model=model)
        pred = max(0.0, pred + _safe_float(error_bound.get("value"), 0.0))
        contributions.sort(key=lambda item: abs(item["contribution"]), reverse=True)
        return {
            "available": True,
            "resource": "memory",
            "model_set": model_set,
            "model_type": model_type,
            "prediction_memory_mb": pred,
            "prediction_memory_bytes": pred * (1024.0 ** 2),
            "prediction_before_error_bound": pred_before_error_bound,
            "prediction_before_anchor_calibration": pred_before_anchor_calibration,
            "baseline_anchor_calibration": anchor_calibration,
            "error_bound": error_bound,
            "feature_count": len(model_features),
            "missing_features": self._missing_features(model_features, features),
            "features": self._selected_features(model_features, features),
            "top_contributions": contributions[:10],
            "metrics": model.get("metrics"),
            "notes": model.get("notes"),
        }

    def predict_io(
        self,
        *,
        target_name: str,
        features: Dict[str, Any],
        workload_context: Dict[str, Any],
    ) -> Prediction:
        if not self.io_model_artifact:
            return {"available": False, "reason": "io_model_disabled_or_missing"}
        model_set = self._io_model_set_name(workload_context)
        model_group = ((self.io_model_artifact.get("models") or {}).get(model_set) or {})
        model = ((model_group.get("targets") or {}).get(target_name) or {})
        pred, breakdown = self._predict_model(
            model=model,
            features=features,
            prediction_key="prediction_mb_per_window",
        )
        if pred is None:
            return {
                "available": False,
                "model_set": model_set,
                "target": target_name,
                "reason": "io_prediction_failed",
            }
        prediction_window_s = self._prediction_window_seconds(workload_context)
        io_mb_s = pred / prediction_window_s
        breakdown.update(
            {
                "available": True,
                "resource": "io",
                "target": target_name,
                "model_set": model_set,
                "metrics": model.get("metrics"),
                "notes": model_group.get("notes"),
                "prediction_mb_per_window": pred,
                "prediction_window_seconds": prediction_window_s,
                "prediction_mb_s": io_mb_s,
                "prediction_bytes_s": io_mb_s * (1024.0 ** 2),
                "target_unit": model.get("target_unit"),
                "prediction_unit": model.get("prediction_unit"),
            }
        )
        return breakdown

    def _load_model_artifact(self, *, section: str, default_path: str) -> Dict[str, Any]:
        section_cfg = self.estimator_cfg.get(section) or {}
        model_cfg = section_cfg.get("learned_model") or {}
        if not bool(model_cfg.get("enabled", False)):
            return {}
        path = _resolve_path(self.root, model_cfg.get("path"), default_path)
        try:
            with path.open("r", encoding="utf-8") as f:
                artifact = json.load(f)
            return artifact if isinstance(artifact, dict) else {}
        except (OSError, json.JSONDecodeError):
            if bool(model_cfg.get("required", False)):
                raise
        return {}

    def _load_joblib_artifact(self, model: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        artifact_path = str(model.get("artifact_path") or "").strip()
        if not artifact_path:
            return None
        path = Path(artifact_path)
        if not path.is_absolute():
            path = self.root / path
        key = str(path)
        if key in self._joblib_model_cache:
            return self._joblib_model_cache[key]
        try:
            import joblib  # type: ignore

            artifact = joblib.load(path)
        except Exception:
            return None
        if not isinstance(artifact, dict):
            return None
        self._joblib_model_cache[key] = artifact
        return artifact

    def _predict_model(
        self,
        *,
        model: Dict[str, Any],
        features: Dict[str, Any],
        prediction_key: str,
    ) -> Tuple[Optional[float], Dict[str, Any]]:
        if not model:
            return None, {}
        artifact = self._load_joblib_artifact(model)
        if artifact:
            pred, breakdown = self._predict_joblib_artifact(
                artifact=artifact,
                model=model,
                features=features,
                prediction_key=prediction_key,
            )
            if pred is not None:
                return pred, breakdown
        return self._predict_linear_artifact(
            model=model,
            features=features,
            prediction_key=prediction_key,
        )

    def _predict_joblib_artifact(
        self,
        *,
        artifact: Dict[str, Any],
        model: Dict[str, Any],
        features: Dict[str, Any],
        prediction_key: str,
    ) -> Tuple[Optional[float], Dict[str, Any]]:
        estimator = artifact.get("model")
        model_features = list(artifact.get("features") or model.get("features") or [])
        if estimator is None or not model_features:
            return None, {}

        values = [_safe_float(features.get(feature), 0.0) for feature in model_features]
        scale_by = artifact.get("feature_scale_by")
        if isinstance(scale_by, dict):
            values = [
                value / max(_safe_float(scale_by.get(feature), 1.0), 1e-12)
                for feature, value in zip(model_features, values)
            ]
        try:
            try:
                import pandas as pd  # type: ignore

                x_input = pd.DataFrame([values], columns=model_features)
            except Exception:
                x_input = [values]
            pred = float(estimator.predict(x_input)[0])
        except Exception:
            return None, {}
        if artifact.get("target_transform") == "log1p_expm1":
            pred = math.expm1(min(50.0, pred))
        pred = max(_safe_float(artifact.get("clip_min"), 0.0), pred)
        pred_before_anchor_calibration = pred
        pred, anchor_calibration = self._apply_baseline_anchor_calibration(model=model, prediction=pred)
        pred_before_error_bound = pred
        error_bound = self._prediction_error_bound(model=model, artifact=artifact)
        pred = max(0.0, pred + _safe_float(error_bound.get("value"), 0.0))
        return pred, {
            "model_type": artifact.get("model_type") or model.get("model_type") or "joblib",
            "benchmark_scoped_absolute_calibration": _safe_bool(
                model.get("benchmark_scoped_absolute_calibration"), False
            ),
            "artifact_path": model.get("artifact_path"),
            prediction_key: pred,
            "prediction_before_error_bound": pred_before_error_bound,
            "prediction_before_anchor_calibration": pred_before_anchor_calibration,
            "baseline_anchor_calibration": anchor_calibration,
            "error_bound": error_bound,
            "feature_count": len(model_features),
            "target_transform": artifact.get("target_transform"),
            "missing_features": self._missing_features(model_features, features),
            "features": self._selected_features(model_features, features),
            "top_feature_values": self._top_feature_values(model_features, values),
        }

    def _predict_linear_artifact(
        self,
        *,
        model: Dict[str, Any],
        features: Dict[str, Any],
        prediction_key: str,
    ) -> Tuple[Optional[float], Dict[str, Any]]:
        model_features = list(model.get("features") or [])
        coef = list(model.get("coef") or [])
        means = list(model.get("feature_mean") or [])
        scales = list(model.get("feature_scale") or [])
        if not model_features or len(model_features) != len(coef):
            return None, {}
        pred = _safe_float(model.get("intercept"), 0.0)
        contributions: List[Dict[str, float]] = []
        for idx, feature in enumerate(model_features):
            raw = _safe_float(features.get(feature), 0.0)
            if model.get("model_type") == "ridge_standardized":
                mean = _safe_float(means[idx] if idx < len(means) else 0.0, 0.0)
                scale = _safe_float(scales[idx] if idx < len(scales) else 1.0, 1.0)
                if abs(scale) < 1e-12:
                    scale = 1.0
                model_value = (raw - mean) / scale
            else:
                model_value = raw
            contribution = _safe_float(coef[idx], 0.0) * model_value
            pred += contribution
            contributions.append(
                {
                    "feature": feature,
                    "raw": raw,
                    "model_value": model_value,
                    "contribution": contribution,
                }
            )
        if model.get("target_transform") == "log1p_expm1":
            pred = math.expm1(min(50.0, pred))
        clip_min = model.get("clip_min")
        if clip_min not in (None, ""):
            pred = max(_safe_float(clip_min), pred)
        pred_before_anchor_calibration = pred
        pred, anchor_calibration = self._apply_baseline_anchor_calibration(model=model, prediction=pred)
        pred_before_error_bound = pred
        error_bound = self._prediction_error_bound(model=model)
        pred = max(0.0, pred + _safe_float(error_bound.get("value"), 0.0))
        contributions.sort(key=lambda item: abs(item["contribution"]), reverse=True)
        return pred, {
            "model_type": model.get("model_type"),
            "benchmark_scoped_absolute_calibration": _safe_bool(
                model.get("benchmark_scoped_absolute_calibration"), False
            ),
            prediction_key: pred,
            "prediction_before_error_bound": pred_before_error_bound,
            "prediction_before_anchor_calibration": pred_before_anchor_calibration,
            "baseline_anchor_calibration": anchor_calibration,
            "error_bound": error_bound,
            "feature_count": len(model_features),
            "missing_features": self._missing_features(model_features, features),
            "features": self._selected_features(model_features, features),
            "top_contributions": contributions[:10],
        }

    def _cpu_model_set_name(self, workload_context: Dict[str, Any]) -> str:
        configured = self._configured_model_set("cpu", workload_context, "cpu_model_set")
        if configured:
            return configured
        family = self._infer_workload_family(workload_context)
        models = self.cpu_model_artifact.get("models") or {}
        if family == "H":
            return "H_serial"
        if family == "CH" and "CH_only" in models:
            return "CH_only"
        if family == "C":
            return "C_only"
        return str(self.cpu_model_artifact.get("default_model_set") or "C_only")

    def _io_model_set_name(self, workload_context: Dict[str, Any]) -> str:
        configured = self._configured_model_set("io", workload_context, "io_model_set")
        if configured:
            return configured.upper()
        family = self._infer_workload_family(workload_context)
        if family in ("H", "C", "CH"):
            return family
        return str(self.io_model_artifact.get("default_model_set") or "CH")

    def _memory_model_set_name(self, workload_context: Dict[str, Any]) -> str:
        configured = self._configured_model_set("memory", workload_context, "memory_model_set")
        if configured:
            return configured
        family = self._infer_workload_family(workload_context)
        if family == "H":
            return "AP_only"
        return str(self.memory_model_artifact.get("default_model_set") or "TP_Hybrid")

    def _configured_model_set(
        self,
        section: str,
        workload_context: Dict[str, Any],
        context_key: str,
    ) -> str:
        section_cfg = self.estimator_cfg.get(section) or {}
        model_cfg = section_cfg.get("learned_model") or {}
        configured = str(model_cfg.get("model_set") or "").strip()
        if configured and configured.lower() != "auto":
            return configured
        explicit = str(workload_context.get(context_key) or "").strip()
        return explicit

    def _infer_workload_family(self, workload_context: Dict[str, Any]) -> str:
        for key in ("model_set", "workload_type", "scenario", "workload_family"):
            value = str(workload_context.get(key) or "").strip()
            normalized = value.lower().replace("-", "_")
            if normalized in ("h", "tpch", "tpc_h", "ap", "runap", "h_serial"):
                return "H"
            if normalized in ("c", "tpcc", "tpc_c", "tp", "runtp", "c_only"):
                return "C"
            if normalized in ("ch", "hc", "mixed", "xp", "runxp", "hybench_xp"):
                return "CH"
        hybench_runtime = workload_context.get("hybench_runtime") or {}
        if isinstance(hybench_runtime, dict):
            mode = str(hybench_runtime.get("mode") or "").strip().lower()
            if mode == "runxp":
                return "CH"
            if mode == "runtp":
                return "C"
            if mode == "runap":
                return "H"
        command_text = " ".join(
            str(workload_context.get(key) or "")
            for key in ("workload_cmd", "benchmark_cmd", "command")
        ).lower()
        if "runxp" in command_text:
            return "CH"
        if "runtp" in command_text or "tpcc" in command_text:
            return "C"
        if "runap" in command_text or "tpcds" in command_text or "tpch" in command_text:
            return "H"
        return "C"

    def _prediction_window_seconds(self, workload_context: Dict[str, Any]) -> float:
        shared_cfg = self.estimator_cfg.get("shared") or {}
        value = (
            workload_context.get("feature_window_seconds")
            or shared_cfg.get("feature_window_seconds")
            or shared_cfg.get("model_feature_window_seconds")
            or 5.0
        )
        return max(1e-6, _safe_float(value, 5.0))

    def _missing_features(self, model_features: List[str], features: Dict[str, Any]) -> List[str]:
        return [feature for feature in model_features if feature not in features][:50]

    def _selected_features(self, model_features: List[str], features: Dict[str, Any]) -> Dict[str, float]:
        return {feature: _safe_float(features.get(feature), 0.0) for feature in model_features}

    def _top_feature_values(self, model_features: List[str], values: List[float]) -> List[Dict[str, float]]:
        return [
            {"feature": feature, "value": value}
            for feature, value in sorted(
                zip(model_features, values),
                key=lambda item: abs(float(item[1])),
                reverse=True,
            )[:10]
        ]

    def _apply_baseline_anchor_calibration(
        self,
        *,
        model: Dict[str, Any],
        prediction: float,
    ) -> Tuple[float, Dict[str, Any]]:
        shared_cfg = self.estimator_cfg.get("shared") or {}
        if not _safe_bool(shared_cfg.get("apply_baseline_anchor_calibration", True), True):
            return prediction, {"applied": False, "source": "disabled"}
        record = (model or {}).get("baseline_anchor_calibration")
        if not isinstance(record, dict):
            return prediction, {"applied": False, "source": "missing"}

        baseline_pred = _safe_float(record.get("predicted_target_before_error_bound"), 0.0)
        actual_target = _safe_float(record.get("actual_target"), 0.0)
        delta_scale = _safe_float(record.get("delta_scale"), 1.0)
        if actual_target <= 0.0:
            return prediction, {"applied": False, "source": "invalid_actual", "detail": record}

        calibrated = max(0.0, actual_target + (float(prediction) - baseline_pred) * delta_scale)
        detail = dict(record)
        detail.update(
            {
                "applied": True,
                "input_prediction": float(prediction),
                "calibrated_prediction": calibrated,
            }
        )
        return calibrated, detail

    def _prediction_error_bound(
        self,
        *,
        model: Dict[str, Any],
        artifact: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        shared_cfg = self.estimator_cfg.get("shared") or {}
        if not _safe_bool(shared_cfg.get("apply_model_error_bound", True), True):
            return {
                "value": 0.0,
                "source": "disabled",
                "applied": False,
                "candidates": [],
            }

        candidates: List[Dict[str, Any]] = []
        for source, container in (
            ("artifact_error_bound", artifact or {}),
            ("model_error_bound", model or {}),
            ("baseline_error_bound", model or {}),
        ):
            key = "baseline_error_bound" if source == "baseline_error_bound" else "error_bound"
            record = (container or {}).get(key)
            if isinstance(record, dict):
                value = _safe_float(record.get("value"), 0.0)
                candidates.append(
                    {
                        "source": source,
                        "value": value,
                        "unit": record.get("unit"),
                        "detail": record,
                    }
                )

        if not candidates:
            return {
                "value": 0.0,
                "source": "missing",
                "applied": False,
                "candidates": [],
            }
        selected = max(candidates, key=lambda item: _safe_float(item.get("value"), 0.0))
        return {
            "value": _safe_float(selected.get("value"), 0.0),
            "unit": selected.get("unit"),
            "source": selected.get("source"),
            "applied": _safe_float(selected.get("value"), 0.0) > 0.0,
            "candidates": candidates,
        }


def predict_resources_from_features(
    feature_input: Dict[str, Any],
    *,
    estimator_cfg: Optional[Dict[str, Any]] = None,
    root: Optional[Path] = None,
) -> Dict[str, Prediction]:
    """Convenience interface for callers that already have estimation features."""
    return ResourceFeaturePredictor(root=root, estimator_cfg=estimator_cfg).predict(feature_input)
