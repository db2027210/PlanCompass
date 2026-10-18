"""Fail-closed direction-first ranking model for real benchmark replays.

This module is deliberately independent from ``parameter_optimizer.py``.  It
does not run a benchmark, change database parameters, or accept an outer-loop
candidate.  It learns only from completed real replays and answers a narrower
question: which candidates are most likely to satisfy both real acceptance
directions?

Three binary outcomes are modelled:

* database QPS strictly increases;
* the target bottleneck utilization strictly decreases; and
* both directions hold for the same replay.

Validation is grouped by ``scope_id``.  A scope represents one outer
incumbent (and its resource/workload context), so every held-out prediction is
made by a model that has not seen any candidate from that incumbent.  Ranking
metrics compare candidates only inside the same held-out scope.

The implementation uses NumPy and the standard library only.  Sparse data,
missing positive/negative classes, a fold with insufficient class support, or
an unconverged model all produce ``ready=False`` and no deployable model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


TASK_QPS_UP = "qps_up"
TASK_TARGET_DOWN = "target_utilization_down"
TASK_JOINT = "joint_direction_success"
TASK_NAMES = (TASK_QPS_UP, TASK_TARGET_DOWN, TASK_JOINT)

_CATEGORY_MISSING = "__MISSING__"
_CATEGORY_UNKNOWN = "__UNKNOWN__"


@dataclass(frozen=True)
class ReplayRankingCandidate:
    """Candidate features available before a real benchmark replay."""

    sample_id: str
    scope_id: str
    parameter_log_delta: Mapping[str, float]
    scenario_features: Mapping[str, Any] = field(default_factory=dict)
    plan_features: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplayRankingSample:
    """Candidate features plus the real replay outcome used for training."""

    sample_id: str
    scope_id: str
    parameter_log_delta: Mapping[str, float]
    db_qps_delta_pct: float
    target_util_delta_pp: float
    scenario_features: Mapping[str, Any] = field(default_factory=dict)
    plan_features: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DirectionRankerConfig:
    """Conservative training, holdout and confidence thresholds."""

    min_samples: int = 12
    min_scopes: int = 3
    min_positive_per_task: int = 3
    min_negative_per_task: int = 3
    min_evaluable_ranking_scopes: int = 2
    max_group_folds: int = 5
    min_db_qps_gain_pct: float = 0.0
    min_target_util_drop_pp: float = 0.0
    probability_threshold: float = 0.5
    confidence_z: float = 1.2815515655446004  # one-sided 90%
    min_direction_accuracy_wilson_lower: float = 0.50
    min_joint_pairwise_auc: float = 0.90
    min_joint_pair_count: int = 10
    max_joint_pairwise_inversion_upper: float = 0.10
    pairwise_cluster_bootstrap_samples: int = 2000
    pairwise_cluster_bootstrap_confidence: float = 0.90
    pairwise_cluster_bootstrap_seed: int = 42
    min_joint_top1_success_wilson_lower: float = 0.50
    l2_penalty: float = 1.0
    max_iter: int = 200
    convergence_tolerance: float = 1e-8

    def validate(self) -> None:
        if self.min_samples <= 0:
            raise ValueError("min_samples must be positive")
        if self.min_scopes < 2:
            raise ValueError("min_scopes must be at least 2")
        if self.min_positive_per_task <= 0:
            raise ValueError("min_positive_per_task must be positive")
        if self.min_negative_per_task <= 0:
            raise ValueError("min_negative_per_task must be positive")
        if self.min_evaluable_ranking_scopes <= 0:
            raise ValueError("min_evaluable_ranking_scopes must be positive")
        if self.max_group_folds < 2:
            raise ValueError("max_group_folds must be at least 2")
        if not math.isfinite(self.min_db_qps_gain_pct):
            raise ValueError("min_db_qps_gain_pct must be finite")
        if self.min_db_qps_gain_pct < 0.0:
            raise ValueError("min_db_qps_gain_pct cannot be negative")
        if not math.isfinite(self.min_target_util_drop_pp):
            raise ValueError("min_target_util_drop_pp must be finite")
        if self.min_target_util_drop_pp < 0.0:
            raise ValueError("min_target_util_drop_pp cannot be negative")
        if not 0.0 < self.probability_threshold < 1.0:
            raise ValueError("probability_threshold must be in (0,1)")
        if not math.isfinite(self.confidence_z) or self.confidence_z <= 0.0:
            raise ValueError("confidence_z must be finite and positive")
        if not 0.0 <= self.min_direction_accuracy_wilson_lower <= 1.0:
            raise ValueError("min_direction_accuracy_wilson_lower must be in [0,1]")
        if not 0.0 <= self.min_joint_pairwise_auc <= 1.0:
            raise ValueError("min_joint_pairwise_auc must be in [0,1]")
        if self.min_joint_pair_count <= 0:
            raise ValueError("min_joint_pair_count must be positive")
        if not 0.0 <= self.max_joint_pairwise_inversion_upper <= 1.0:
            raise ValueError("max_joint_pairwise_inversion_upper must be in [0,1]")
        if self.pairwise_cluster_bootstrap_samples <= 0:
            raise ValueError("pairwise_cluster_bootstrap_samples must be positive")
        if not 0.0 < self.pairwise_cluster_bootstrap_confidence < 1.0:
            raise ValueError(
                "pairwise_cluster_bootstrap_confidence must be in (0,1)"
            )
        if not 0.0 <= self.min_joint_top1_success_wilson_lower <= 1.0:
            raise ValueError(
                "min_joint_top1_success_wilson_lower must be in [0,1]"
            )
        if not math.isfinite(self.l2_penalty) or self.l2_penalty <= 0.0:
            raise ValueError("l2_penalty must be finite and positive")
        if self.max_iter <= 0:
            raise ValueError("max_iter must be positive")
        if (
            not math.isfinite(self.convergence_tolerance)
            or self.convergence_tolerance <= 0.0
        ):
            raise ValueError("convergence_tolerance must be finite and positive")


@dataclass(frozen=True)
class BinaryLogisticModel:
    """A fitted ridge-logistic model with an explicit intercept."""

    intercept: float
    coefficients: Tuple[float, ...]
    iterations: int
    converged: bool
    final_loss: float

    def predict_probability(self, matrix: np.ndarray) -> np.ndarray:
        coefficients = np.asarray(self.coefficients, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] != coefficients.shape[0]:
            raise ValueError("feature matrix does not match model coefficients")
        logits = self.intercept + matrix @ coefficients
        return _sigmoid(logits)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureEncoder:
    """Training-only schema and normalization for candidate features."""

    numeric_feature_keys: Tuple[str, ...]
    numeric_means: Tuple[float, ...]
    numeric_scales: Tuple[float, ...]
    categorical_feature_keys: Tuple[str, ...]
    categorical_values: Mapping[str, Tuple[str, ...]]
    feature_names: Tuple[str, ...]

    @classmethod
    def fit(cls, samples: Sequence[ReplayRankingCandidate]) -> "FeatureEncoder":
        if not samples:
            raise ValueError("cannot fit a feature encoder without samples")
        numeric_schema: Dict[str, List[float]] = {}
        numeric_presence: Dict[str, List[float]] = {}
        categorical_schema: Dict[str, set] = {}

        rows = [_raw_feature_row(sample) for sample in samples]
        all_keys = sorted({key for row in rows for key in row})
        for key in all_keys:
            kinds = {
                _feature_value_kind(row[key])
                for row in rows
                if key in row and row[key] is not None
            }
            if len(kinds) > 1:
                raise ValueError(f"feature {key!r} mixes numeric and categorical values")
            kind = next(iter(kinds), "categorical")
            if kind == "numeric":
                values: List[float] = []
                presence: List[float] = []
                for row in rows:
                    if key in row and row[key] is not None:
                        values.append(_finite_float(row[key], key))
                        presence.append(1.0)
                    else:
                        values.append(0.0)
                        presence.append(0.0)
                numeric_schema[key] = values
                numeric_presence[key] = presence
            else:
                categorical_schema[key] = {
                    _category_token(row.get(key)) for row in rows
                }

        numeric_keys: List[str] = []
        means: List[float] = []
        scales: List[float] = []
        feature_names: List[str] = []
        for key in sorted(numeric_schema):
            values = np.asarray(numeric_schema[key], dtype=float)
            mean = float(np.mean(values))
            scale = float(np.std(values))
            if scale < 1e-12:
                scale = 1.0
            numeric_keys.append(key)
            means.append(mean)
            scales.append(scale)
            feature_names.append(key)

            # Parameter deltas are structurally zero when absent.  Optional
            # scenario/plan values receive an explicit presence indicator.
            if not key.startswith("parameter_log_delta:"):
                presence_key = f"presence:{key}"
                presence_values = np.asarray(numeric_presence[key], dtype=float)
                presence_mean = float(np.mean(presence_values))
                presence_scale = float(np.std(presence_values))
                if presence_scale < 1e-12:
                    presence_scale = 1.0
                numeric_keys.append(presence_key)
                means.append(presence_mean)
                scales.append(presence_scale)
                feature_names.append(presence_key)

        categorical_values: Dict[str, Tuple[str, ...]] = {}
        for key in sorted(categorical_schema):
            values = set(categorical_schema[key])
            values.update({_CATEGORY_MISSING, _CATEGORY_UNKNOWN})
            ordered = tuple(sorted(values))
            categorical_values[key] = ordered
            feature_names.extend(f"{key}={value}" for value in ordered)

        return cls(
            numeric_feature_keys=tuple(numeric_keys),
            numeric_means=tuple(means),
            numeric_scales=tuple(scales),
            categorical_feature_keys=tuple(sorted(categorical_schema)),
            categorical_values=categorical_values,
            feature_names=tuple(feature_names),
        )

    def transform(self, samples: Sequence[ReplayRankingCandidate]) -> np.ndarray:
        rows: List[List[float]] = []
        numeric_base_keys = {
            key for key in self.numeric_feature_keys if not key.startswith("presence:")
        }
        for sample in samples:
            raw = _raw_feature_row(sample)
            encoded: List[float] = []
            for key, mean, scale in zip(
                self.numeric_feature_keys,
                self.numeric_means,
                self.numeric_scales,
            ):
                if key.startswith("presence:"):
                    base_key = key[len("presence:") :]
                    value = 1.0 if base_key in raw and raw[base_key] is not None else 0.0
                else:
                    value = (
                        _finite_float(raw[key], key)
                        if key in raw and raw[key] is not None
                        else 0.0
                    )
                encoded.append((value - mean) / scale)

            for key in self.categorical_feature_keys:
                observed = _category_token(raw.get(key))
                allowed = self.categorical_values[key]
                if observed not in allowed:
                    observed = _CATEGORY_UNKNOWN
                encoded.extend(1.0 if observed == value else 0.0 for value in allowed)
            rows.append(encoded)

        if not rows:
            return np.zeros((0, len(self.feature_names)), dtype=float)
        matrix = np.asarray(rows, dtype=float)
        if matrix.shape[1] != len(self.feature_names):
            raise RuntimeError(
                "encoder schema mismatch: "
                f"matrix={matrix.shape[1]}, names={len(self.feature_names)}, "
                f"numeric_base={len(numeric_base_keys)}"
            )
        return matrix

    def to_dict(self) -> Dict[str, Any]:
        return {
            "numeric_feature_keys": list(self.numeric_feature_keys),
            "numeric_means": list(self.numeric_means),
            "numeric_scales": list(self.numeric_scales),
            "categorical_feature_keys": list(self.categorical_feature_keys),
            "categorical_values": {
                key: list(values)
                for key, values in sorted(self.categorical_values.items())
            },
            "feature_names": list(self.feature_names),
        }


@dataclass(frozen=True)
class RealReplayDirectionRanker:
    """Deployable model; candidates rank by direct joint-success probability."""

    encoder: FeatureEncoder
    task_models: Mapping[str, BinaryLogisticModel]
    min_db_qps_gain_pct: float
    min_target_util_drop_pp: float

    def predict(
        self,
        samples: Sequence[ReplayRankingCandidate],
    ) -> List[Dict[str, Any]]:
        normalized = _normalize_candidates(samples, require_unique_ids=True)
        matrix = self.encoder.transform(normalized)
        probabilities = {
            task: self.task_models[task].predict_probability(matrix)
            for task in TASK_NAMES
        }
        predictions = []
        for index, sample in enumerate(normalized):
            predictions.append(
                {
                    "sample_id": sample.sample_id,
                    "scope_id": sample.scope_id,
                    "qps_up_probability": float(probabilities[TASK_QPS_UP][index]),
                    "target_utilization_down_probability": float(
                        probabilities[TASK_TARGET_DOWN][index]
                    ),
                    "joint_direction_success_probability": float(
                        probabilities[TASK_JOINT][index]
                    ),
                    "ranking_score": float(probabilities[TASK_JOINT][index]),
                }
            )
        return predictions

    def rank(
        self,
        samples: Sequence[ReplayRankingCandidate],
    ) -> List[Dict[str, Any]]:
        predictions = self.predict(samples)
        return sorted(
            predictions,
            key=lambda item: (-item["ranking_score"], item["sample_id"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_type": "numpy_ridge_logistic_three_direction_v1",
            "ranking_score": "joint_direction_success_probability",
            "encoder": self.encoder.to_dict(),
            "task_models": {
                task: self.task_models[task].to_dict() for task in TASK_NAMES
            },
            "label_thresholds": {
                "min_db_qps_gain_pct": self.min_db_qps_gain_pct,
                "min_target_util_drop_pp": self.min_target_util_drop_pp,
            },
        }


@dataclass(frozen=True)
class DirectionRankerTrainingResult:
    """Auditable group-heldout result and optional deployable ranker."""

    ready: bool
    reason: str
    fail_closed_reasons: Tuple[str, ...]
    dataset_audit: Mapping[str, Any]
    fold_audit: Sequence[Mapping[str, Any]]
    heldout_metrics: Mapping[str, Any]
    heldout_predictions: Sequence[Mapping[str, Any]]
    model: Optional[RealReplayDirectionRanker]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "artifact_type": "real_replay_direction_ranker_group_holdout_v1",
            "ready": self.ready,
            "reason": self.reason,
            "fail_closed_reasons": list(self.fail_closed_reasons),
            "dataset_audit": dict(self.dataset_audit),
            "fold_audit": [dict(item) for item in self.fold_audit],
            "heldout_metrics": dict(self.heldout_metrics),
            "heldout_predictions": [dict(item) for item in self.heldout_predictions],
            "model": None if self.model is None else self.model.to_dict(),
        }


def wilson_lower_bound(successes: int, total: int, z: float) -> float:
    """Return a one-sided Wilson lower bound for a Bernoulli proportion."""

    if total < 0 or successes < 0 or successes > total:
        raise ValueError("invalid Wilson successes/total")
    if not math.isfinite(z) or z <= 0.0:
        raise ValueError("Wilson z must be finite and positive")
    if total == 0:
        return 0.0
    proportion = successes / total
    z_squared = z * z
    denominator = 1.0 + z_squared / total
    centre = proportion + z_squared / (2.0 * total)
    radius = z * math.sqrt(
        proportion * (1.0 - proportion) / total
        + z_squared / (4.0 * total * total)
    )
    return max(0.0, (centre - radius) / denominator)


def train_real_replay_direction_ranker(
    samples: Sequence[ReplayRankingSample],
    *,
    config: Optional[DirectionRankerConfig] = None,
) -> DirectionRankerTrainingResult:
    """Train and group-validate a direction-first replay ranker.

    The function never returns a model unless all three outcomes have enough
    positive and negative support globally and in every training fold.
    """

    cfg = config or DirectionRankerConfig()
    cfg.validate()
    normalized = _normalize_samples(samples, require_unique_ids=True)
    labels = _label_arrays(normalized, cfg)
    scopes = sorted({sample.scope_id for sample in normalized})
    audit = _dataset_audit(normalized, labels, cfg)

    reasons: List[str] = []
    if len(normalized) < cfg.min_samples:
        reasons.append(
            f"insufficient_samples:{len(normalized)}<{cfg.min_samples}"
        )
    if len(scopes) < cfg.min_scopes:
        reasons.append(f"insufficient_scopes:{len(scopes)}<{cfg.min_scopes}")
    reasons.extend(_class_support_failures(labels, cfg, prefix="global"))
    if reasons:
        return _failed_result(reasons, audit)

    fold_scope_ids = _build_group_folds(normalized, cfg.max_group_folds)
    heldout_rows: List[Dict[str, Any]] = []
    fold_audit: List[Dict[str, Any]] = []

    for fold_index, heldout_scopes in enumerate(fold_scope_ids):
        heldout_set = set(heldout_scopes)
        train_indices = [
            index
            for index, sample in enumerate(normalized)
            if sample.scope_id not in heldout_set
        ]
        heldout_indices = [
            index
            for index, sample in enumerate(normalized)
            if sample.scope_id in heldout_set
        ]
        train_scopes = sorted({normalized[index].scope_id for index in train_indices})
        overlap = sorted(set(train_scopes).intersection(heldout_set))
        train_labels = {
            task: labels[task][train_indices] for task in TASK_NAMES
        }
        fold_reasons = _class_support_failures(
            train_labels,
            cfg,
            prefix=f"fold_{fold_index}_train",
        )
        if not train_indices or not heldout_indices:
            fold_reasons.append(f"fold_{fold_index}_empty_train_or_holdout")
        if overlap:
            fold_reasons.append(f"fold_{fold_index}_scope_leakage")
        if fold_reasons:
            reasons.extend(fold_reasons)
            break

        training_samples = [normalized[index] for index in train_indices]
        validation_samples = [normalized[index] for index in heldout_indices]
        encoder = FeatureEncoder.fit(training_samples)
        train_matrix = encoder.transform(training_samples)
        heldout_matrix = encoder.transform(validation_samples)
        task_models: Dict[str, BinaryLogisticModel] = {}
        task_probabilities: Dict[str, np.ndarray] = {}
        for task in TASK_NAMES:
            model = _fit_binary_logistic(train_matrix, train_labels[task], cfg)
            task_models[task] = model
            if not model.converged:
                reasons.append(f"fold_{fold_index}_{task}_did_not_converge")
                break
            task_probabilities[task] = model.predict_probability(heldout_matrix)
        if reasons:
            break

        fold_audit.append(
            {
                "fold_index": fold_index,
                "train_scope_ids": train_scopes,
                "heldout_scope_ids": list(heldout_scopes),
                "scope_overlap": overlap,
                "train_sample_count": len(train_indices),
                "heldout_sample_count": len(heldout_indices),
                "train_class_support": {
                    task: _class_counts(train_labels[task]) for task in TASK_NAMES
                },
                "heldout_class_support": {
                    task: _class_counts(labels[task][heldout_indices])
                    for task in TASK_NAMES
                },
                "feature_count": len(encoder.feature_names),
                "task_models": {
                    task: {
                        "converged": task_models[task].converged,
                        "iterations": task_models[task].iterations,
                        "final_loss": task_models[task].final_loss,
                    }
                    for task in TASK_NAMES
                },
            }
        )

        for local_index, global_index in enumerate(heldout_indices):
            sample = normalized[global_index]
            heldout_rows.append(
                {
                    "sample_id": sample.sample_id,
                    "scope_id": sample.scope_id,
                    "fold_index": fold_index,
                    "labels": {
                        task: int(labels[task][global_index]) for task in TASK_NAMES
                    },
                    "probabilities": {
                        task: float(task_probabilities[task][local_index])
                        for task in TASK_NAMES
                    },
                }
            )

    if reasons:
        return _failed_result(reasons, audit, fold_audit=fold_audit)
    if len(heldout_rows) != len(normalized):
        return _failed_result(
            [
                "incomplete_group_holdout_predictions:"
                f"{len(heldout_rows)}!={len(normalized)}"
            ],
            audit,
            fold_audit=fold_audit,
        )

    heldout_rows.sort(key=lambda item: item["sample_id"])
    heldout_metrics = {
        "evaluation_contract": (
            "out_of_scope_predictions_only; ranking comparisons only within "
            "the same heldout outer incumbent scope"
        ),
        "group_holdout_prediction_count": len(heldout_rows),
        "scope_count": len(scopes),
        "fold_count": len(fold_audit),
        "scope_leakage_detected": any(item["scope_overlap"] for item in fold_audit),
        "tasks": {
            task: _heldout_task_metrics(heldout_rows, task, cfg)
            for task in TASK_NAMES
        },
    }
    joint_ranking = heldout_metrics["tasks"][TASK_JOINT]["ranking"]
    evaluable_joint_scopes = int(joint_ranking["evaluable_pair_scope_count"])
    if evaluable_joint_scopes < cfg.min_evaluable_ranking_scopes:
        return _failed_result(
            [
                "insufficient_heldout_joint_ranking_scopes:"
                f"{evaluable_joint_scopes}<{cfg.min_evaluable_ranking_scopes}"
            ],
            audit,
            fold_audit=fold_audit,
            heldout_metrics=heldout_metrics,
            heldout_predictions=heldout_rows,
        )

    admission_failures: List[str] = []
    for task in (TASK_QPS_UP, TASK_TARGET_DOWN):
        accuracy_lower = float(
            heldout_metrics["tasks"][task]["accuracy_wilson_lower"]
        )
        if accuracy_lower < cfg.min_direction_accuracy_wilson_lower:
            admission_failures.append(
                f"heldout_{task}_accuracy_wilson_lower_below_threshold:"
                f"{accuracy_lower:.6f}<{cfg.min_direction_accuracy_wilson_lower:.6f}"
            )
    joint_pair_count = int(joint_ranking["pair_count"])
    joint_pairwise_auc = joint_ranking["pairwise_auc_micro"]
    if joint_pair_count < cfg.min_joint_pair_count:
        admission_failures.append(
            f"heldout_joint_pair_count_below_threshold:"
            f"{joint_pair_count}<{cfg.min_joint_pair_count}"
        )
    if (
        joint_pairwise_auc is None
        or float(joint_pairwise_auc) < cfg.min_joint_pairwise_auc
    ):
        admission_failures.append(
            f"heldout_joint_pairwise_auc_below_threshold:"
            f"{joint_pairwise_auc}<{cfg.min_joint_pairwise_auc:.6f}"
        )
    joint_inversion_upper = joint_ranking.get(
        "pairwise_inversion_cluster_bootstrap_upper"
    )
    if (
        joint_inversion_upper is None
        or float(joint_inversion_upper) > cfg.max_joint_pairwise_inversion_upper
    ):
        admission_failures.append(
            "heldout_joint_pairwise_inversion_cluster_upper_above_threshold:"
            f"{joint_inversion_upper}>"
            f"{cfg.max_joint_pairwise_inversion_upper:.6f}"
        )
    top1_lower = float(joint_ranking["top1_success_wilson_lower"])
    if top1_lower < cfg.min_joint_top1_success_wilson_lower:
        admission_failures.append(
            f"heldout_joint_top1_wilson_lower_below_threshold:"
            f"{top1_lower:.6f}<"
            f"{cfg.min_joint_top1_success_wilson_lower:.6f}"
        )
    heldout_metrics["deployment_admission"] = {
        "passed": not admission_failures,
        "thresholds": {
            "min_direction_accuracy_wilson_lower": (
                cfg.min_direction_accuracy_wilson_lower
            ),
            "min_joint_pairwise_auc": cfg.min_joint_pairwise_auc,
            "max_joint_pairwise_inversion_rate": 1.0 - cfg.min_joint_pairwise_auc,
            "min_joint_pair_count": cfg.min_joint_pair_count,
            "max_joint_pairwise_inversion_upper": (
                cfg.max_joint_pairwise_inversion_upper
            ),
            "pairwise_cluster_bootstrap_samples": (
                cfg.pairwise_cluster_bootstrap_samples
            ),
            "pairwise_cluster_bootstrap_confidence": (
                cfg.pairwise_cluster_bootstrap_confidence
            ),
            "min_joint_top1_success_wilson_lower": (
                cfg.min_joint_top1_success_wilson_lower
            ),
        },
        "failures": admission_failures,
    }
    if admission_failures:
        return _failed_result(
            admission_failures,
            audit,
            fold_audit=fold_audit,
            heldout_metrics=heldout_metrics,
            heldout_predictions=heldout_rows,
        )

    final_encoder = FeatureEncoder.fit(normalized)
    final_matrix = final_encoder.transform(normalized)
    final_models: Dict[str, BinaryLogisticModel] = {}
    for task in TASK_NAMES:
        model = _fit_binary_logistic(final_matrix, labels[task], cfg)
        if not model.converged:
            return _failed_result(
                [f"final_{task}_did_not_converge"],
                audit,
                fold_audit=fold_audit,
                heldout_metrics=heldout_metrics,
                heldout_predictions=heldout_rows,
            )
        final_models[task] = model

    ranker = RealReplayDirectionRanker(
        encoder=final_encoder,
        task_models=final_models,
        min_db_qps_gain_pct=cfg.min_db_qps_gain_pct,
        min_target_util_drop_pp=cfg.min_target_util_drop_pp,
    )
    return DirectionRankerTrainingResult(
        ready=True,
        reason="group_heldout_validation_complete",
        fail_closed_reasons=(),
        dataset_audit=audit,
        fold_audit=tuple(fold_audit),
        heldout_metrics=heldout_metrics,
        heldout_predictions=tuple(heldout_rows),
        model=ranker,
    )


def _failed_result(
    reasons: Sequence[str],
    audit: Mapping[str, Any],
    *,
    fold_audit: Sequence[Mapping[str, Any]] = (),
    heldout_metrics: Optional[Mapping[str, Any]] = None,
    heldout_predictions: Sequence[Mapping[str, Any]] = (),
) -> DirectionRankerTrainingResult:
    unique_reasons = tuple(dict.fromkeys(str(reason) for reason in reasons))
    return DirectionRankerTrainingResult(
        ready=False,
        reason=unique_reasons[0] if unique_reasons else "fail_closed",
        fail_closed_reasons=unique_reasons,
        dataset_audit=dict(audit),
        fold_audit=tuple(dict(item) for item in fold_audit),
        heldout_metrics=dict(heldout_metrics or {}),
        heldout_predictions=tuple(dict(item) for item in heldout_predictions),
        model=None,
    )


def _normalize_samples(
    samples: Sequence[ReplayRankingSample],
    *,
    require_unique_ids: bool,
) -> List[ReplayRankingSample]:
    normalized: List[ReplayRankingSample] = []
    for sample in samples:
        sample_id = str(sample.sample_id).strip()
        scope_id = str(sample.scope_id).strip()
        if not sample_id:
            raise ValueError("sample_id cannot be empty")
        if not scope_id:
            raise ValueError("scope_id cannot be empty")
        if not isinstance(sample.parameter_log_delta, Mapping):
            raise ValueError("parameter_log_delta must be a mapping")
        parameter_delta = {
            str(key): _finite_float(value, f"parameter_log_delta[{key!r}]")
            for key, value in sample.parameter_log_delta.items()
        }
        if not parameter_delta or not any(abs(value) > 1e-15 for value in parameter_delta.values()):
            raise ValueError("parameter_log_delta must contain a non-zero movement")
        scenario = _normalize_optional_features(sample.scenario_features, "scenario")
        plan = _normalize_optional_features(sample.plan_features, "plan")
        normalized.append(
            ReplayRankingSample(
                sample_id=sample_id,
                scope_id=scope_id,
                parameter_log_delta=parameter_delta,
                db_qps_delta_pct=_finite_float(
                    sample.db_qps_delta_pct,
                    "db_qps_delta_pct",
                ),
                target_util_delta_pp=_finite_float(
                    sample.target_util_delta_pp,
                    "target_util_delta_pp",
                ),
                scenario_features=scenario,
                plan_features=plan,
            )
        )
    if require_unique_ids:
        ids = [sample.sample_id for sample in normalized]
        if len(ids) != len(set(ids)):
            raise ValueError("sample_id values must be unique")
    return normalized


def _normalize_candidates(
    samples: Sequence[ReplayRankingCandidate],
    *,
    require_unique_ids: bool,
) -> List[ReplayRankingCandidate]:
    normalized: List[ReplayRankingCandidate] = []
    for sample in samples:
        sample_id = str(sample.sample_id).strip()
        scope_id = str(sample.scope_id).strip()
        if not sample_id:
            raise ValueError("sample_id cannot be empty")
        if not scope_id:
            raise ValueError("scope_id cannot be empty")
        if not isinstance(sample.parameter_log_delta, Mapping):
            raise ValueError("parameter_log_delta must be a mapping")
        parameter_delta = {
            str(key): _finite_float(value, f"parameter_log_delta[{key!r}]")
            for key, value in sample.parameter_log_delta.items()
        }
        if not parameter_delta or not any(
            abs(value) > 1e-15 for value in parameter_delta.values()
        ):
            raise ValueError("parameter_log_delta must contain a non-zero movement")
        normalized.append(
            ReplayRankingCandidate(
                sample_id=sample_id,
                scope_id=scope_id,
                parameter_log_delta=parameter_delta,
                scenario_features=_normalize_optional_features(
                    sample.scenario_features,
                    "scenario",
                ),
                plan_features=_normalize_optional_features(
                    sample.plan_features,
                    "plan",
                ),
            )
        )
    if require_unique_ids:
        ids = [sample.sample_id for sample in normalized]
        if len(ids) != len(set(ids)):
            raise ValueError("sample_id values must be unique")
    return normalized


def _normalize_optional_features(
    values: Mapping[str, Any],
    namespace: str,
) -> Dict[str, Any]:
    if not isinstance(values, Mapping):
        raise ValueError(f"{namespace}_features must be a mapping")
    normalized: Dict[str, Any] = {}
    for raw_key, value in values.items():
        key = str(raw_key).strip()
        if not key:
            raise ValueError(f"{namespace}_features contains an empty key")
        if value is not None and not isinstance(value, (str, bool, int, float, np.number)):
            raise ValueError(
                f"{namespace}_features[{key!r}] must be a scalar or None"
            )
        if isinstance(value, (int, float, np.number)) and not isinstance(value, bool):
            value = _finite_float(value, f"{namespace}_features[{key!r}]")
        normalized[key] = value
    return normalized


def _finite_float(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _feature_value_kind(value: Any) -> str:
    if isinstance(value, bool):
        return "categorical"
    if isinstance(value, (int, float, np.number)):
        return "numeric"
    return "categorical"


def _category_token(value: Any) -> str:
    if value is None:
        return _CATEGORY_MISSING
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _raw_feature_row(sample: ReplayRankingCandidate) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        f"parameter_log_delta:{name}": value
        for name, value in sample.parameter_log_delta.items()
    }
    row.update(
        {
            f"scenario:{name}": value
            for name, value in sample.scenario_features.items()
        }
    )
    row.update(
        {f"plan:{name}": value for name, value in sample.plan_features.items()}
    )
    return row


def _label_arrays(
    samples: Sequence[ReplayRankingSample],
    config: DirectionRankerConfig,
) -> Dict[str, np.ndarray]:
    qps = np.asarray(
        [sample.db_qps_delta_pct > config.min_db_qps_gain_pct for sample in samples],
        dtype=float,
    )
    target = np.asarray(
        [
            sample.target_util_delta_pp < -config.min_target_util_drop_pp
            for sample in samples
        ],
        dtype=float,
    )
    return {
        TASK_QPS_UP: qps,
        TASK_TARGET_DOWN: target,
        TASK_JOINT: qps * target,
    }


def _class_counts(labels: np.ndarray) -> Dict[str, int]:
    positives = int(np.sum(labels))
    return {
        "sample_count": int(labels.shape[0]),
        "positive_count": positives,
        "negative_count": int(labels.shape[0]) - positives,
    }


def _class_support_failures(
    labels: Mapping[str, np.ndarray],
    config: DirectionRankerConfig,
    *,
    prefix: str,
) -> List[str]:
    failures: List[str] = []
    for task in TASK_NAMES:
        counts = _class_counts(labels[task])
        if counts["positive_count"] == 0:
            failures.append(f"{prefix}_{task}_has_no_positive")
        elif counts["positive_count"] < config.min_positive_per_task:
            failures.append(
                f"{prefix}_{task}_insufficient_positive:"
                f"{counts['positive_count']}<{config.min_positive_per_task}"
            )
        if counts["negative_count"] == 0:
            failures.append(f"{prefix}_{task}_has_no_negative")
        elif counts["negative_count"] < config.min_negative_per_task:
            failures.append(
                f"{prefix}_{task}_insufficient_negative:"
                f"{counts['negative_count']}<{config.min_negative_per_task}"
            )
    return failures


def _dataset_audit(
    samples: Sequence[ReplayRankingSample],
    labels: Mapping[str, np.ndarray],
    config: DirectionRankerConfig,
) -> Dict[str, Any]:
    scope_counts: Dict[str, int] = {}
    for sample in samples:
        scope_counts[sample.scope_id] = scope_counts.get(sample.scope_id, 0) + 1
    return {
        "sample_count": len(samples),
        "scope_count": len(scope_counts),
        "samples_per_scope": dict(sorted(scope_counts.items())),
        "task_class_support": {
            task: _class_counts(labels[task]) for task in TASK_NAMES
        },
        "label_thresholds": {
            "qps_up": f"db_qps_delta_pct > {config.min_db_qps_gain_pct}",
            "target_utilization_down": (
                f"target_util_delta_pp < {-config.min_target_util_drop_pp}"
            ),
            "joint": "qps_up AND target_utilization_down",
        },
        "group_key": "scope_id (one outer incumbent and its context)",
    }


def _stable_scope_hash(scope_id: str) -> str:
    return hashlib.sha256(scope_id.encode("utf-8")).hexdigest()


def _build_group_folds(
    samples: Sequence[ReplayRankingSample],
    max_group_folds: int,
) -> List[Tuple[str, ...]]:
    counts: Dict[str, int] = {}
    for sample in samples:
        counts[sample.scope_id] = counts.get(sample.scope_id, 0) + 1
    fold_count = min(max_group_folds, len(counts))
    bins: List[List[str]] = [[] for _ in range(fold_count)]
    bin_sizes = [0 for _ in range(fold_count)]
    ordered_scopes = sorted(
        counts,
        key=lambda scope: (-counts[scope], _stable_scope_hash(scope)),
    )
    for scope in ordered_scopes:
        index = min(range(fold_count), key=lambda item: (bin_sizes[item], item))
        bins[index].append(scope)
        bin_sizes[index] += counts[scope]
    return [tuple(sorted(group)) for group in bins]


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _logistic_loss(
    design: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
    l2_penalty: float,
) -> float:
    logits = design @ weights
    data_loss = float(np.mean(np.logaddexp(0.0, logits) - labels * logits))
    penalty = 0.5 * l2_penalty * float(np.dot(weights[1:], weights[1:]))
    return data_loss + penalty


def _fit_binary_logistic(
    matrix: np.ndarray,
    labels: np.ndarray,
    config: DirectionRankerConfig,
) -> BinaryLogisticModel:
    if matrix.ndim != 2 or labels.ndim != 1 or matrix.shape[0] != labels.shape[0]:
        raise ValueError("invalid feature matrix or labels")
    if matrix.shape[0] == 0:
        raise ValueError("cannot fit an empty model")
    design = np.column_stack([np.ones(matrix.shape[0], dtype=float), matrix])
    weights = np.zeros(design.shape[1], dtype=float)
    identity = np.eye(design.shape[1], dtype=float)
    identity[0, 0] = 0.0
    previous_loss = _logistic_loss(design, labels, weights, config.l2_penalty)
    converged = False
    iteration = 0

    for iteration in range(1, config.max_iter + 1):
        probabilities = _sigmoid(design @ weights)
        gradient = design.T @ (probabilities - labels) / design.shape[0]
        gradient += config.l2_penalty * identity @ weights
        curvature = probabilities * (1.0 - probabilities)
        hessian = (design.T * curvature) @ design / design.shape[0]
        hessian += config.l2_penalty * identity
        hessian += np.eye(design.shape[1], dtype=float) * 1e-10
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(hessian, gradient, rcond=None)[0]

        step_scale = 1.0
        accepted = False
        candidate_loss = previous_loss
        candidate_weights = weights
        for _ in range(25):
            trial = weights - step_scale * step
            trial_loss = _logistic_loss(design, labels, trial, config.l2_penalty)
            if math.isfinite(trial_loss) and trial_loss <= previous_loss + 1e-12:
                accepted = True
                candidate_loss = trial_loss
                candidate_weights = trial
                break
            step_scale *= 0.5
        if not accepted:
            break

        improvement = previous_loss - candidate_loss
        weight_change = float(np.max(np.abs(candidate_weights - weights)))
        weights = candidate_weights
        previous_loss = candidate_loss
        if (
            improvement <= config.convergence_tolerance
            and weight_change <= math.sqrt(config.convergence_tolerance)
        ):
            converged = True
            break

    return BinaryLogisticModel(
        intercept=float(weights[0]),
        coefficients=tuple(float(value) for value in weights[1:]),
        iterations=iteration,
        converged=converged,
        final_loss=float(previous_loss),
    )


def _heldout_task_metrics(
    rows: Sequence[Mapping[str, Any]],
    task: str,
    config: DirectionRankerConfig,
) -> Dict[str, Any]:
    labels = np.asarray([row["labels"][task] for row in rows], dtype=int)
    scores = np.asarray([row["probabilities"][task] for row in rows], dtype=float)
    predicted = scores >= config.probability_threshold
    actual_positive = labels == 1
    true_positive = int(np.sum(predicted & actual_positive))
    true_negative = int(np.sum((~predicted) & (~actual_positive)))
    predicted_positive_count = int(np.sum(predicted))
    positive_count = int(np.sum(actual_positive))
    correct = true_positive + true_negative
    total = len(rows)
    epsilon = 1e-12
    clipped = np.clip(scores, epsilon, 1.0 - epsilon)
    log_loss = -float(
        np.mean(labels * np.log(clipped) + (1 - labels) * np.log(1.0 - clipped))
    )
    brier = float(np.mean((scores - labels) ** 2))
    return {
        "sample_count": total,
        "positive_count": positive_count,
        "negative_count": total - positive_count,
        "observed_positive_rate": positive_count / total,
        "observed_positive_wilson_lower": wilson_lower_bound(
            positive_count,
            total,
            config.confidence_z,
        ),
        "probability_threshold": config.probability_threshold,
        "accuracy": correct / total,
        "accuracy_wilson_lower": wilson_lower_bound(
            correct,
            total,
            config.confidence_z,
        ),
        "predicted_positive_count": predicted_positive_count,
        "precision": (
            true_positive / predicted_positive_count if predicted_positive_count else 0.0
        ),
        "precision_wilson_lower": wilson_lower_bound(
            true_positive,
            predicted_positive_count,
            config.confidence_z,
        ),
        "recall": true_positive / positive_count if positive_count else 0.0,
        "recall_wilson_lower": wilson_lower_bound(
            true_positive,
            positive_count,
            config.confidence_z,
        ),
        "brier_score": brier,
        "log_loss": log_loss,
        "ranking": _group_ranking_metrics(
            rows,
            task,
            config.confidence_z,
            bootstrap_samples=config.pairwise_cluster_bootstrap_samples,
            bootstrap_confidence=config.pairwise_cluster_bootstrap_confidence,
            bootstrap_seed=config.pairwise_cluster_bootstrap_seed,
        ),
    }


def _group_ranking_metrics(
    rows: Sequence[Mapping[str, Any]],
    task: str,
    confidence_z: float,
    *,
    bootstrap_samples: int,
    bootstrap_confidence: float,
    bootstrap_seed: int,
) -> Dict[str, Any]:
    by_scope: Dict[str, List[Mapping[str, Any]]] = {}
    for row in rows:
        by_scope.setdefault(str(row["scope_id"]), []).append(row)

    pair_credit = 0.0
    pair_count = 0
    scope_aucs: List[float] = []
    scope_average_precisions: List[float] = []
    top1_successes = 0
    top1_eligible_scopes = 0
    no_positive_scopes = 0
    no_negative_scopes = 0
    per_scope: List[Dict[str, Any]] = []
    scope_pair_stats: List[Tuple[int, float]] = []

    for scope_id in sorted(by_scope):
        scope_rows = by_scope[scope_id]
        labels = np.asarray([row["labels"][task] for row in scope_rows], dtype=int)
        scores = np.asarray(
            [row["probabilities"][task] for row in scope_rows],
            dtype=float,
        )
        positive_scores = scores[labels == 1]
        negative_scores = scores[labels == 0]
        scope_pairs = len(positive_scores) * len(negative_scores)
        scope_credit = 0.0
        if scope_pairs:
            differences = positive_scores[:, None] - negative_scores[None, :]
            scope_credit = float(
                np.sum(differences > 0.0) + 0.5 * np.sum(differences == 0.0)
            )
            scope_auc = scope_credit / scope_pairs
            scope_aucs.append(scope_auc)
            scope_pair_stats.append((scope_pairs, scope_pairs - scope_credit))
            pair_credit += scope_credit
            pair_count += scope_pairs
        else:
            scope_auc = None
        if len(positive_scores) == 0:
            no_positive_scopes += 1
            average_precision = None
        else:
            order = sorted(
                range(len(scope_rows)),
                key=lambda index: (
                    -scores[index],
                    str(scope_rows[index]["sample_id"]),
                ),
            )
            positives_seen = 0
            precision_sum = 0.0
            for rank, index in enumerate(order, start=1):
                if labels[index] == 1:
                    positives_seen += 1
                    precision_sum += positives_seen / rank
            average_precision = precision_sum / len(positive_scores)
            scope_average_precisions.append(average_precision)

            top1_eligible_scopes += 1
            maximum = float(np.max(scores))
            top_tie_labels = labels[np.isclose(scores, maximum, rtol=0.0, atol=1e-15)]
            # Conservative: a tied top is successful only when every tied
            # candidate is positive for the task.
            top1_successes += int(len(top_tie_labels) > 0 and np.all(top_tie_labels == 1))
        if len(negative_scores) == 0:
            no_negative_scopes += 1
        per_scope.append(
            {
                "scope_id": scope_id,
                "sample_count": len(scope_rows),
                "positive_count": int(np.sum(labels)),
                "negative_count": int(len(labels) - np.sum(labels)),
                "pair_count": scope_pairs,
                "pairwise_auc": scope_auc,
                "average_precision": average_precision,
            }
        )

    inversion_bootstrap_upper: Optional[float] = None
    if scope_pair_stats:
        rng = np.random.RandomState(int(bootstrap_seed))
        draw_values: List[float] = []
        scope_count = len(scope_pair_stats)
        for _ in range(int(bootstrap_samples)):
            chosen = rng.randint(0, scope_count, size=scope_count)
            draw_pairs = sum(scope_pair_stats[index][0] for index in chosen)
            draw_loss = sum(scope_pair_stats[index][1] for index in chosen)
            if draw_pairs > 0:
                draw_values.append(float(draw_loss / draw_pairs))
        if draw_values:
            inversion_bootstrap_upper = float(
                np.quantile(
                    np.asarray(draw_values, dtype=float),
                    float(bootstrap_confidence),
                )
            )

    return {
        "comparison_scope": "within_same_heldout_outer_incumbent_only",
        "scope_count": len(by_scope),
        "evaluable_pair_scope_count": len(scope_aucs),
        "pair_count": pair_count,
        "pairwise_auc_micro": pair_credit / pair_count if pair_count else None,
        "pairwise_auc_macro": (
            float(np.mean(scope_aucs)) if scope_aucs else None
        ),
        "pairwise_inversion_rate": (
            1.0 - pair_credit / pair_count if pair_count else None
        ),
        "pairwise_inversion_cluster_bootstrap_upper": inversion_bootstrap_upper,
        "pairwise_cluster_bootstrap_samples": int(bootstrap_samples),
        "pairwise_cluster_bootstrap_confidence": float(bootstrap_confidence),
        "pairwise_cluster_unit": "heldout_outer_incumbent_scope",
        "mean_average_precision": (
            float(np.mean(scope_average_precisions))
            if scope_average_precisions
            else None
        ),
        "top1_eligible_scope_count": top1_eligible_scopes,
        "top1_success_count": top1_successes,
        "top1_success_rate": (
            top1_successes / top1_eligible_scopes if top1_eligible_scopes else 0.0
        ),
        "top1_success_wilson_lower": wilson_lower_bound(
            top1_successes,
            top1_eligible_scopes,
            confidence_z,
        ),
        "scope_without_positive_count": no_positive_scopes,
        "scope_without_negative_count": no_negative_scopes,
        "per_scope": per_scope,
    }


def training_result_to_json(
    result: DirectionRankerTrainingResult,
    *,
    indent: Optional[int] = 2,
) -> str:
    """Serialize an auditable training result without NumPy objects."""

    return json.dumps(
        result.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        indent=indent,
    )


def direction_ranker_from_dict(payload: Mapping[str, Any]) -> RealReplayDirectionRanker:
    """Reconstruct a ranker from a validated JSON artifact model section."""

    if not isinstance(payload, Mapping):
        raise ValueError("direction ranker model payload must be an object")
    if payload.get("model_type") != "numpy_ridge_logistic_three_direction_v1":
        raise ValueError("unsupported direction ranker model_type")
    encoder_raw = payload.get("encoder")
    task_models_raw = payload.get("task_models")
    thresholds = payload.get("label_thresholds")
    if not isinstance(encoder_raw, Mapping):
        raise ValueError("direction ranker encoder is missing")
    if not isinstance(task_models_raw, Mapping):
        raise ValueError("direction ranker task_models are missing")
    if not isinstance(thresholds, Mapping):
        raise ValueError("direction ranker label_thresholds are missing")

    categorical_raw = encoder_raw.get("categorical_values")
    if not isinstance(categorical_raw, Mapping):
        raise ValueError("direction ranker categorical_values are missing")
    encoder = FeatureEncoder(
        numeric_feature_keys=tuple(
            str(value) for value in encoder_raw.get("numeric_feature_keys") or []
        ),
        numeric_means=tuple(
            _finite_float(value, "encoder.numeric_means")
            for value in encoder_raw.get("numeric_means") or []
        ),
        numeric_scales=tuple(
            _finite_float(value, "encoder.numeric_scales")
            for value in encoder_raw.get("numeric_scales") or []
        ),
        categorical_feature_keys=tuple(
            str(value)
            for value in encoder_raw.get("categorical_feature_keys") or []
        ),
        categorical_values={
            str(key): tuple(str(value) for value in values)
            for key, values in categorical_raw.items()
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes))
        },
        feature_names=tuple(
            str(value) for value in encoder_raw.get("feature_names") or []
        ),
    )
    if not (
        len(encoder.numeric_feature_keys)
        == len(encoder.numeric_means)
        == len(encoder.numeric_scales)
    ):
        raise ValueError("direction ranker numeric encoder lengths do not match")
    if any(scale <= 0.0 for scale in encoder.numeric_scales):
        raise ValueError("direction ranker numeric scales must be positive")
    if set(encoder.categorical_feature_keys) != set(encoder.categorical_values):
        raise ValueError("direction ranker categorical encoder schema does not match")

    task_models: Dict[str, BinaryLogisticModel] = {}
    for task in TASK_NAMES:
        raw = task_models_raw.get(task)
        if not isinstance(raw, Mapping):
            raise ValueError(f"direction ranker task model is missing: {task}")
        coefficients = tuple(
            _finite_float(value, f"task_models.{task}.coefficients")
            for value in raw.get("coefficients") or []
        )
        if len(coefficients) != len(encoder.feature_names):
            raise ValueError(f"direction ranker coefficient length mismatch: {task}")
        model = BinaryLogisticModel(
            intercept=_finite_float(raw.get("intercept"), f"{task}.intercept"),
            coefficients=coefficients,
            iterations=int(raw.get("iterations") or 0),
            converged=raw.get("converged") is True,
            final_loss=_finite_float(raw.get("final_loss"), f"{task}.final_loss"),
        )
        if not model.converged:
            raise ValueError(f"direction ranker task model did not converge: {task}")
        task_models[task] = model

    return RealReplayDirectionRanker(
        encoder=encoder,
        task_models=task_models,
        min_db_qps_gain_pct=_finite_float(
            thresholds.get("min_db_qps_gain_pct"),
            "label_thresholds.min_db_qps_gain_pct",
        ),
        min_target_util_drop_pp=_finite_float(
            thresholds.get("min_target_util_drop_pp"),
            "label_thresholds.min_target_util_drop_pp",
        ),
    )


__all__ = [
    "TASK_QPS_UP",
    "TASK_TARGET_DOWN",
    "TASK_JOINT",
    "ReplayRankingCandidate",
    "ReplayRankingSample",
    "DirectionRankerConfig",
    "DirectionRankerTrainingResult",
    "RealReplayDirectionRanker",
    "train_real_replay_direction_ranker",
    "training_result_to_json",
    "direction_ranker_from_dict",
    "wilson_lower_bound",
]
