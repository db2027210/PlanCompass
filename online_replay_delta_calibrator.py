from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from phase2_delta_calibrator import COST_PARAM_KEYS, extract_plan_features


RESOURCE_NAMES = ("cpu", "memory", "io")
TARGET_NAMES = ("db_qps_delta_pct", "cpu_delta_pp", "memory_delta_pp", "io_delta_pp")


def _finite_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _candidate_resource(candidate: Dict[str, Any]) -> Dict[str, float]:
    raw = (
        candidate.get("predicted_resource")
        or candidate.get("resource")
        or candidate.get("estimated_resource")
        or {}
    )
    return {
        name: float(raw.get(name, 0.0) or 0.0)
        for name in RESOURCE_NAMES
    }


def _candidate_metadata(candidate: Dict[str, Any]) -> Dict[str, Any]:
    raw = candidate.get("metadata") or candidate.get("estimation_metadata") or {}
    return raw if isinstance(raw, dict) else {}


def _actual_resource(evaluation: Dict[str, Any]) -> Dict[str, float]:
    validation = evaluation.get("candidate_final_validation") or {}
    raw = validation.get("resource_normalized") or evaluation.get("actual_resource") or {}
    result: Dict[str, float] = {}
    for name in RESOURCE_NAMES:
        value = _finite_float(raw.get(name))
        if value is not None:
            result[name] = value
    return result


def _db_qps_delta(evaluation: Dict[str, Any]) -> Optional[float]:
    status = evaluation.get("candidate_primary_target_status") or {}
    return _finite_float(status.get("db_qps_delta_pct"))


def _signed_log1p(value: float) -> float:
    return math.copysign(math.log1p(abs(value)), value)


def _pairwise_inversion(actual: Sequence[float], predicted: Sequence[float]) -> Optional[float]:
    pairs = 0
    inversions = 0
    for left in range(len(actual)):
        for right in range(left + 1, len(actual)):
            actual_delta = actual[left] - actual[right]
            predicted_delta = predicted[left] - predicted[right]
            if abs(actual_delta) <= 1e-12 or abs(predicted_delta) <= 1e-12:
                continue
            pairs += 1
            if actual_delta * predicted_delta < 0.0:
                inversions += 1
    return inversions / pairs if pairs else None


def _direction_accuracy(actual: Sequence[float], predicted: Sequence[float]) -> Optional[float]:
    if not actual:
        return None
    correct = 0
    for actual_value, predicted_value in zip(actual, predicted):
        actual_sign = 0 if abs(actual_value) < 0.10 else (1 if actual_value > 0 else -1)
        predicted_sign = 0 if abs(predicted_value) < 0.10 else (1 if predicted_value > 0 else -1)
        correct += int(actual_sign == predicted_sign)
    return correct / len(actual)


def _ridge_fit(x: np.ndarray, y: np.ndarray, alpha: float) -> Dict[str, np.ndarray]:
    center = np.mean(x, axis=0)
    scale = np.std(x, axis=0)
    scale = np.where(scale > 1e-9, scale, 1.0)
    normalized = (x - center) / scale
    design = np.column_stack([np.ones(len(normalized)), normalized])
    penalty = np.eye(design.shape[1], dtype=float) * float(alpha)
    penalty[0, 0] = 0.0
    coefficients = np.linalg.pinv(design.T @ design + penalty) @ design.T @ y
    return {"center": center, "scale": scale, "coefficients": coefficients}


def _ridge_predict(model: Dict[str, np.ndarray], x: np.ndarray) -> np.ndarray:
    normalized = (x - model["center"]) / model["scale"]
    design = np.column_stack([np.ones(len(normalized)), normalized])
    return design @ model["coefficients"]


class OnlineReplayDeltaCalibrator:
    """Fit a small-sample delta model from real replay and rerank unseen candidates."""

    def __init__(
        self,
        *,
        baseline_params: Dict[str, float],
        baseline_resource: Dict[str, float],
        capability: Dict[str, float],
        target_resource: str,
        min_samples: int = 12,
        max_plan_features: int = 6,
        max_target_mae_pp: float = 5.0,
        max_other_mae_pp: float = 8.0,
        min_target_direction_accuracy: float = 0.70,
        max_db_qps_inversion: float = 0.35,
    ) -> None:
        self.baseline_params = dict(baseline_params)
        self.baseline_resource = dict(baseline_resource)
        self.capability = dict(capability)
        self.target_resource = str(target_resource)
        self.min_samples = max(4, int(min_samples))
        self.max_plan_features = max(0, int(max_plan_features))
        self.max_target_mae_pp = float(max_target_mae_pp)
        self.max_other_mae_pp = float(max_other_mae_pp)
        self.min_target_direction_accuracy = float(min_target_direction_accuracy)
        self.max_db_qps_inversion = float(max_db_qps_inversion)

    def _select_plan_features(self, candidates: Sequence[Dict[str, Any]]) -> List[str]:
        rows = [extract_plan_features(_candidate_metadata(candidate)) for candidate in candidates]
        names = sorted({name for row in rows for name in row})
        scored: List[Tuple[float, str]] = []
        for name in names:
            present = [row[name] for row in rows if name in row]
            if len(present) < max(3, int(math.ceil(0.75 * len(rows)))):
                continue
            transformed = np.asarray([_signed_log1p(value) for value in present], dtype=float)
            variance = float(np.var(transformed))
            if variance > 1e-12:
                scored.append((variance, name))
        scored.sort(reverse=True)
        return [name for _, name in scored[: self.max_plan_features]]

    def _feature_row(self, candidate: Dict[str, Any], plan_feature_names: Sequence[str]) -> List[float]:
        params = candidate.get("params") or {}
        row: List[float] = []
        for name in COST_PARAM_KEYS:
            baseline = max(float(self.baseline_params.get(name, 1.0) or 1.0), 1e-12)
            value = max(float(params.get(name, baseline) or baseline), 1e-12)
            row.append(math.log(value / baseline))
        predicted = _candidate_resource(candidate)
        for name in RESOURCE_NAMES:
            capability = max(float(self.capability.get(name, 1.0) or 1.0), 1e-12)
            delta_pp = (predicted[name] - float(self.baseline_resource.get(name, 0.0))) / capability * 100.0
            row.append(delta_pp)
        plan_features = extract_plan_features(_candidate_metadata(candidate))
        for name in plan_feature_names:
            row.append(_signed_log1p(float(plan_features.get(name, 0.0) or 0.0)))
        return row

    def _training_rows(
        self,
        candidates: Sequence[Dict[str, Any]],
        evaluations: Sequence[Dict[str, Any]],
        training_samples: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, float]]]:
        selected_candidates: List[Dict[str, Any]] = []
        labels: List[Dict[str, float]] = []
        seen = set()

        def append_pair(candidate: Dict[str, Any], evaluation: Dict[str, Any]) -> None:
            params = candidate.get("params") or {}
            signature = tuple(
                (name, round(float(params.get(name, 0.0) or 0.0), 15))
                for name in COST_PARAM_KEYS
            )
            if signature in seen:
                return
            actual = _actual_resource(evaluation)
            qps_delta = _db_qps_delta(evaluation)
            if qps_delta is None or any(name not in actual for name in RESOURCE_NAMES):
                return
            label = {"db_qps_delta_pct": qps_delta}
            for name in RESOURCE_NAMES:
                capability = max(float(self.capability.get(name, 1.0) or 1.0), 1e-12)
                label[f"{name}_delta_pp"] = (
                    actual[name] - float(self.baseline_resource.get(name, 0.0))
                ) / capability * 100.0
            seen.add(signature)
            selected_candidates.append(candidate)
            labels.append(label)

        for sample in training_samples or []:
            if not isinstance(sample, dict):
                continue
            candidate = sample.get("candidate") or {}
            evaluation = sample.get("evaluation") or {}
            if isinstance(candidate, dict) and isinstance(evaluation, dict):
                append_pair(candidate, evaluation)

        for evaluation in evaluations:
            index = evaluation.get("_candidate_index")
            if not isinstance(index, int) or index < 0 or index >= len(candidates):
                continue
            append_pair(candidates[index], evaluation)
        return selected_candidates, labels

    def fit_and_apply(
        self,
        *,
        candidates: List[Dict[str, Any]],
        evaluations: Sequence[Dict[str, Any]],
        training_samples: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        training_candidates, label_rows = self._training_rows(
            candidates,
            evaluations,
            training_samples=training_samples,
        )
        sample_count = len(training_candidates)
        if sample_count < self.min_samples:
            return {
                "available": False,
                "sample_count": sample_count,
                "min_samples": self.min_samples,
                "training_gate_passed": False,
                "delta_calibration_requires_expansion": True,
                "reason": "minimum_real_replay_evidence_not_reached",
            }

        plan_feature_names = self._select_plan_features(training_candidates)
        x = np.asarray(
            [self._feature_row(candidate, plan_feature_names) for candidate in training_candidates],
            dtype=float,
        )
        y = np.asarray(
            [[label[name] for name in TARGET_NAMES] for label in label_rows],
            dtype=float,
        )
        target_scale = np.std(y, axis=0)
        target_scale = np.where(target_scale > 1e-6, target_scale, 1.0)

        best_alpha = 1.0
        best_loss = float("inf")
        best_loo = np.zeros_like(y)
        for alpha in (0.05, 0.2, 1.0, 5.0, 20.0):
            loo = np.zeros_like(y)
            for held_out in range(sample_count):
                mask = np.ones(sample_count, dtype=bool)
                mask[held_out] = False
                model = _ridge_fit(x[mask], y[mask], alpha)
                loo[held_out] = _ridge_predict(model, x[held_out : held_out + 1])[0]
            normalized_error = (loo - y) / target_scale
            loss = float(np.mean(normalized_error ** 2))
            qps_inversion = _pairwise_inversion(y[:, 0].tolist(), loo[:, 0].tolist())
            if qps_inversion is not None:
                loss += qps_inversion
            if loss < best_loss:
                best_loss = loss
                best_alpha = alpha
                best_loo = loo

        residual = np.abs(best_loo - y)
        mae = np.mean(residual, axis=0)
        p90 = np.quantile(residual, 0.90, axis=0)
        qps_inversion = _pairwise_inversion(y[:, 0].tolist(), best_loo[:, 0].tolist())
        direction_accuracy = {
            name: _direction_accuracy(y[:, index].tolist(), best_loo[:, index].tolist())
            for index, name in enumerate(RESOURCE_NAMES, start=1)
        }
        mae_by_target = {name: float(mae[index]) for index, name in enumerate(TARGET_NAMES)}
        p90_by_target = {name: float(p90[index]) for index, name in enumerate(TARGET_NAMES)}
        if self.target_resource in RESOURCE_NAMES:
            target_mae = mae_by_target[f"{self.target_resource}_delta_pp"]
            target_direction_accuracy = direction_accuracy.get(self.target_resource) or 0.0
            other_mae = max(
                mae_by_target[f"{name}_delta_pp"]
                for name in RESOURCE_NAMES
                if name != self.target_resource
            )
        else:
            target_mae = max(mae_by_target[f"{name}_delta_pp"] for name in RESOURCE_NAMES)
            target_direction_accuracy = min(
                direction_accuracy.get(name) or 0.0
                for name in RESOURCE_NAMES
            )
            other_mae = target_mae
        gate_reasons: List[str] = []
        if target_mae > self.max_target_mae_pp:
            gate_reasons.append("target_delta_mae_above_threshold")
        if other_mae > self.max_other_mae_pp:
            gate_reasons.append("non_target_delta_mae_above_threshold")
        if target_direction_accuracy < self.min_target_direction_accuracy:
            gate_reasons.append("target_direction_accuracy_below_threshold")
        if qps_inversion is None or qps_inversion > self.max_db_qps_inversion:
            gate_reasons.append("db_qps_pairwise_inversion_above_threshold")
        training_gate_passed = not gate_reasons

        final_model = _ridge_fit(x, y, best_alpha)
        all_x = np.asarray(
            [self._feature_row(candidate, plan_feature_names) for candidate in candidates],
            dtype=float,
        )
        all_prediction = _ridge_predict(final_model, all_x)
        target_index = (
            RESOURCE_NAMES.index(self.target_resource) + 1
            if self.target_resource in RESOURCE_NAMES
            else None
        )
        for index, candidate in enumerate(candidates):
            prediction = all_prediction[index]
            predicted_resource: Dict[str, float] = {}
            for resource_index, name in enumerate(RESOURCE_NAMES, start=1):
                capability = float(self.capability.get(name, 1.0) or 1.0)
                predicted_resource[name] = max(
                    0.0,
                    float(self.baseline_resource.get(name, 0.0))
                    + prediction[resource_index] * capability / 100.0,
                )
            other_up = max(
                prediction[RESOURCE_NAMES.index(name) + 1]
                for name in RESOURCE_NAMES
                if name != self.target_resource
            )
            if target_index is not None:
                target_down = -prediction[target_index]
                uncertainty_penalty = p90[target_index] + 0.25 * p90[0]
                rank_score = (
                    10.0 * prediction[0]
                    + 3.0 * target_down
                    + 1.5 * max(0.0, other_up)
                    - 2.0 * uncertainty_penalty
                )
            else:
                resource_up = sum(max(0.0, prediction[index]) for index in range(1, 4))
                uncertainty_penalty = max(p90[1:4]) + 0.25 * p90[0]
                rank_score = (
                    10.0 * prediction[0]
                    + 1.5 * resource_up
                    - 2.0 * uncertainty_penalty
                )
            candidate["predicted_resource"] = predicted_resource
            candidate["predicted_db_qps_delta_pct"] = float(prediction[0])
            candidate["_adaptive_replay_rank_score"] = float(rank_score)
            candidate["online_real_delta_calibration"] = {
                "source": "online_real_replay_ridge_v1",
                "sample_count": sample_count,
                "prediction": {
                    name: float(prediction[target_idx])
                    for target_idx, name in enumerate(TARGET_NAMES)
                },
                "predicted_resource": predicted_resource,
                "uncertainty_p90": dict(p90_by_target),
                "training_gate_passed": training_gate_passed,
            }

        ranked_indices = sorted(
            range(len(candidates)),
            key=lambda index: float(
                candidates[index].get("_adaptive_replay_rank_score", 0.0) or 0.0
            ),
            reverse=True,
        )
        for rank, candidate_index in enumerate(ranked_indices, start=1):
            candidates[candidate_index]["online_calibrated_rank"] = rank

        return {
            "available": True,
            "source": "online_real_replay_ridge_v1",
            "sample_count": sample_count,
            "min_samples": self.min_samples,
            "selected_alpha": best_alpha,
            "selected_plan_features": plan_feature_names,
            "feature_count": int(x.shape[1]),
            "loo_normalized_loss": best_loss,
            "loo_mae": mae_by_target,
            "loo_abs_error_p90": p90_by_target,
            "loo_direction_accuracy": direction_accuracy,
            "loo_db_qps_pairwise_inversion_rate": qps_inversion,
            "training_gate_passed": training_gate_passed,
            "gate_reasons": gate_reasons,
            "delta_calibration_requires_expansion": not training_gate_passed,
        }
