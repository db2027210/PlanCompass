from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional


COST_PARAM_KEYS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def extract_plan_features(metadata: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """Extract aggregate estimator inputs without including predicted outputs."""
    if not isinstance(metadata, dict):
        return {}
    plan_metadata = metadata.get("plan_resource_estimation")
    if not isinstance(plan_metadata, dict):
        plan_metadata = metadata.get("estimation_metadata")
    if not isinstance(plan_metadata, dict):
        plan_metadata = metadata
    breakdown = plan_metadata.get("resource_breakdown") or {}
    features: Dict[str, float] = {}
    for resource_name in ("cpu", "memory", "io", "io_read", "io_write"):
        item = breakdown.get(resource_name) or {}
        calibration = item.get("calibration_breakdown") or {}
        learned = (
            calibration.get("learned_model")
            or calibration.get("learned_model_fallback")
            or {}
        )
        values = learned.get("features") or {}
        if not isinstance(values, dict):
            continue
        for name, value in values.items():
            number = _safe_float(value, float("nan"))
            if math.isfinite(number):
                features.setdefault(str(name), number)
    return dict(sorted(features.items()))


class Phase2DeltaCalibrator:
    """Small-sample RBF calibrator for baseline-relative VM metrics."""

    def __init__(self, artifact: Dict[str, Any]):
        self.artifact = artifact
        self.baseline_params = artifact.get("baseline_params") or {}
        self.baseline_plan_features = artifact.get("baseline_plan_features") or {}
        self.baseline_resource = artifact.get("baseline_resource") or {}
        self.feature_names = list(artifact.get("feature_names") or [])
        self.feature_scales = [
            max(_safe_float(value, 1.0), 1e-12)
            for value in (artifact.get("feature_scales") or [])
        ]
        self.points = list(artifact.get("points") or [])
        self.bandwidth = max(_safe_float(artifact.get("bandwidth"), 1.0), 1e-6)
        support = artifact.get("support") or {}
        self.max_nearest_distance = max(
            _safe_float(support.get("max_nearest_distance"), float("inf")),
            0.0,
        )
        gate = artifact.get("training_gate") or {}
        self.training_gate_passed = bool(gate.get("passed", False))
        if len(self.feature_names) != len(self.feature_scales):
            raise ValueError("delta calibrator feature_names/feature_scales length mismatch")
        if not self.points:
            raise ValueError("delta calibrator contains no points")

    @classmethod
    def from_path(cls, path: Path) -> "Phase2DeltaCalibrator":
        with path.open("r", encoding="utf-8") as handle:
            artifact = json.load(handle)
        if not isinstance(artifact, dict):
            raise ValueError(f"invalid delta calibrator artifact: {path}")
        return cls(artifact)

    def _raw_feature_value(
        self,
        name: str,
        params: Dict[str, float],
        plan_features: Dict[str, float],
        baseline_plan_features: Dict[str, float],
    ) -> float:
        if name.startswith("param_log:"):
            param_name = name.split(":", 1)[1]
            baseline = max(_safe_float(self.baseline_params.get(param_name), 0.0), 1e-12)
            value = max(_safe_float(params.get(param_name), baseline), 1e-12)
            return math.log(value / baseline)
        if name.startswith("plan_delta:"):
            feature_name = name.split(":", 1)[1]
            baseline = _safe_float(baseline_plan_features.get(feature_name), 0.0)
            return _safe_float(plan_features.get(feature_name), baseline) - baseline
        return 0.0

    def vector(
        self,
        *,
        params: Dict[str, float],
        plan_features: Dict[str, float],
        baseline_plan_features: Optional[Dict[str, float]] = None,
    ) -> List[float]:
        plan_baseline = (
            baseline_plan_features
            if isinstance(baseline_plan_features, dict) and baseline_plan_features
            else self.baseline_plan_features
        )
        return [
            self._raw_feature_value(name, params, plan_features, plan_baseline) / scale
            for name, scale in zip(self.feature_names, self.feature_scales)
        ]

    def predict(
        self,
        *,
        params: Dict[str, float],
        metadata: Optional[Dict[str, Any]] = None,
        plan_features: Optional[Dict[str, float]] = None,
        baseline_resource: Optional[Dict[str, float]] = None,
        baseline_plan_features: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        supplied_features = plan_features or extract_plan_features(metadata)
        required_plan = [
            name.split(":", 1)[1]
            for name in self.feature_names
            if name.startswith("plan_delta:")
        ]
        present_count = sum(name in supplied_features for name in required_plan)
        coverage = (
            present_count / len(required_plan)
            if required_plan
            else 1.0
        )
        x = self.vector(
            params=params,
            plan_features=supplied_features,
            baseline_plan_features=baseline_plan_features,
        )

        distances: List[float] = []
        weights: List[float] = []
        compatible_points: List[Dict[str, Any]] = []
        for point in self.points:
            point_x = [_safe_float(value) for value in (point.get("x") or [])]
            if len(point_x) != len(x):
                continue
            distance_sq = sum((left - right) ** 2 for left, right in zip(point_x, x))
            distance = math.sqrt(max(0.0, distance_sq))
            distances.append(distance)
            weights.append(math.exp(-distance_sq / (2.0 * self.bandwidth ** 2)))
            compatible_points.append(point)
        if not weights:
            return {
                "available": False,
                "reason": "no_compatible_calibration_points",
                "plan_feature_coverage": coverage,
            }
        nearest = min(range(len(distances)), key=distances.__getitem__)
        total_weight = sum(weights)
        if distances[nearest] <= 1e-10:
            weights = [1.0 if index == nearest else 0.0 for index in range(len(weights))]
            total_weight = 1.0
        elif total_weight <= 1e-12:
            weights = [1.0 if index == nearest else 0.0 for index in range(len(weights))]
            total_weight = 1.0
        weights = [weight / total_weight for weight in weights]

        targets = ("db_qps_delta_pct", "cpu_delta_pct", "memory_delta_pct", "io_delta_pct")
        predictions: Dict[str, float] = {}
        uncertainties: Dict[str, float] = {}
        for target in targets:
            values = [
                _safe_float((point.get("labels") or {}).get(target), 0.0)
                for point in compatible_points
            ]
            prediction = sum(weight * value for weight, value in zip(weights, values))
            variance = sum(
                weight * (value - prediction) ** 2
                for weight, value in zip(weights, values)
            )
            predictions[target] = prediction
            uncertainties[target] = math.sqrt(max(0.0, variance))

        resource_baseline = (
            baseline_resource
            if isinstance(baseline_resource, dict) and baseline_resource
            else self.baseline_resource
        )
        resource_baseline_source = (
            "current_runtime_baseline"
            if isinstance(baseline_resource, dict) and baseline_resource
            else "artifact_baseline"
        )
        predicted_resource: Dict[str, float] = {}
        for resource_name in ("cpu", "memory", "io"):
            baseline = _safe_float(resource_baseline.get(resource_name), 0.0)
            delta_pct = predictions[f"{resource_name}_delta_pct"]
            predicted_resource[resource_name] = max(0.0, baseline * (1.0 + delta_pct / 100.0))

        nearest_distance = min(distances)
        within_support = nearest_distance <= self.max_nearest_distance
        return {
            "available": True,
            "training_gate_passed": self.training_gate_passed,
            "plan_feature_coverage": coverage,
            "nearest_distance": nearest_distance,
            "max_nearest_distance": self.max_nearest_distance,
            "within_support": within_support,
            "bandwidth": self.bandwidth,
            "prediction": predictions,
            "uncertainty": uncertainties,
            "resource_baseline_source": resource_baseline_source,
            "plan_feature_baseline_source": (
                "current_runtime_baseline"
                if isinstance(baseline_plan_features, dict)
                and baseline_plan_features
                else "artifact_baseline"
            ),
            "predicted_resource_baseline": {
                name: _safe_float(resource_baseline.get(name), 0.0)
                for name in ("cpu", "memory", "io")
            },
            "predicted_resource": predicted_resource,
            "source": "fixed_workload_real_delta_rbf_v1",
        }
