#!/usr/bin/env python3
"""Train fail-closed PostgreSQL resource-direction models per scenario.

The training target is the signed utilization delta between a real candidate
replay and its real incumbent.  Models are isolated by workload, scenario and
the exact resource-capability profile.  Validation always holds out complete
incumbent scopes; candidate rows from one incumbent are never split between
training and validation.

This script intentionally does not overwrite the existing absolute resource
estimator.  It writes a separate experimental artifact whose per-resource
``ready`` flag is true only when a confidence/coverage gate passes.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from real_replay_direction_ranker import FeatureEncoder, ReplayRankingCandidate  # noqa: E402
from shell.build_resource_estimation_agent_experience import (  # noqa: E402
    RESOURCES,
    _fresh_observations,
    _optimizer_observations,
)


DEFAULT_AUDIT = ROOT / "docs" / "artifacts" / "pg_real_replay_direction_audit_20260723.json"
DEFAULT_OUTPUT = ROOT / "docs" / "artifacts" / "pg_scenario_resource_direction_model_20260723.json"
DEFAULT_REPORT = ROOT / "docs" / "pg_scenario_resource_direction_model_20260723.md"

DIRECTION_NAMES = ("decrease", "near_zero", "increase")
L2_GRID = (0.01, 0.1, 1.0, 10.0)
CONFIDENCE_THRESHOLDS = tuple(round(0.50 + index * 0.05, 2) for index in range(10))
CONFIDENCE_Z = 1.2815515655446004  # one-sided 90%


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _capability_token(capability: Mapping[str, Any]) -> str:
    normalized = {
        resource: _finite(capability.get(resource))
        for resource in RESOURCES
    }
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _direction_index(delta_pp: float, epsilon_pp: float) -> int:
    if delta_pp < -epsilon_pp:
        return 0
    if delta_pp > epsilon_pp:
        return 2
    return 1


def _candidate(row: Mapping[str, Any]) -> ReplayRankingCandidate:
    plan_evidence = row.get("plan_evidence")
    if not isinstance(plan_evidence, Mapping):
        plan_evidence = {}
    plan_delta = plan_evidence.get("plan_feature_delta")
    if not isinstance(plan_delta, Mapping):
        plan_delta = {}
    node_delta = plan_delta.get("node_type_count_delta")
    if not isinstance(node_delta, Mapping):
        node_delta = {}

    scenario_features: Dict[str, Any] = {
        "incumbent_cpu_utilization_pct": _finite(
            row["incumbent_utilization_pct"].get("cpu")
        ),
        "incumbent_memory_utilization_pct": _finite(
            row["incumbent_utilization_pct"].get("memory")
        ),
        "incumbent_io_utilization_pct": _finite(
            row["incumbent_utilization_pct"].get("io")
        ),
        "changed_parameter_count": int(row.get("changed_parameter_count") or 0),
        "phase": str(row.get("phase") or "unknown"),
        "evidence_kind": str(row.get("evidence_kind") or "unknown"),
        "stability_class": str(row.get("stability_class") or "unknown"),
    }
    plan_features: Dict[str, Any] = {
        "available": bool(plan_evidence.get("available")),
        "changed_plan_count": _finite(plan_evidence.get("changed_plan_count")),
        "plan_rows_sum_proxy_delta": _finite(
            plan_delta.get("plan_rows_sum_proxy_delta")
        ),
        "plan_width_sum_proxy_delta": _finite(
            plan_delta.get("plan_width_sum_proxy_delta")
        ),
        "total_cost_sum_proxy_delta": _finite(
            plan_delta.get("total_cost_sum_proxy_delta")
        ),
    }
    for name, value in node_delta.items():
        plan_features[f"node_type_count_delta:{name}"] = _finite(value)

    return ReplayRankingCandidate(
        sample_id=str(row["sample_id"]),
        scope_id=str(row["incumbent_scope_id"]),
        parameter_log_delta={
            str(name): _finite(value)
            for name, value in row["parameter_log_delta"].items()
        },
        scenario_features=scenario_features,
        plan_features=plan_features,
    )


def _scope_weights(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    counts = Counter(str(row["incumbent_scope_id"]) for row in rows)
    weights = np.asarray(
        [1.0 / counts[str(row["incumbent_scope_id"])] for row in rows],
        dtype=float,
    )
    return weights * (len(weights) / float(np.sum(weights)))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _fit_weighted_binary_logistic(
    matrix: np.ndarray,
    labels: np.ndarray,
    base_weights: np.ndarray,
    *,
    l2_penalty: float,
    max_iter: int = 150,
    tolerance: float = 1e-8,
) -> Dict[str, Any]:
    """Fit one class-vs-rest model with scope and class balancing."""

    positives = int(np.sum(labels == 1.0))
    negatives = int(np.sum(labels == 0.0))
    if positives == 0:
        return {"kind": "constant", "probability": 0.0}
    if negatives == 0:
        return {"kind": "constant", "probability": 1.0}

    class_weights = np.where(
        labels == 1.0,
        len(labels) / (2.0 * positives),
        len(labels) / (2.0 * negatives),
    )
    sample_weights = base_weights * class_weights
    sample_weights *= len(sample_weights) / float(np.sum(sample_weights))

    design = np.column_stack([np.ones(matrix.shape[0], dtype=float), matrix])
    weights = np.zeros(design.shape[1], dtype=float)
    identity = np.eye(design.shape[1], dtype=float)
    identity[0, 0] = 0.0
    converged = False
    iteration = 0

    for iteration in range(1, max_iter + 1):
        probabilities = _sigmoid(design @ weights)
        residual = sample_weights * (probabilities - labels)
        gradient = design.T @ residual / float(np.sum(sample_weights))
        gradient += l2_penalty * identity @ weights
        curvature = sample_weights * probabilities * (1.0 - probabilities)
        hessian = (design.T * curvature) @ design / float(np.sum(sample_weights))
        hessian += l2_penalty * identity
        hessian += np.eye(design.shape[1], dtype=float) * 1e-10
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(hessian, gradient, rcond=None)[0]
        weights -= step
        if float(np.max(np.abs(step))) <= tolerance:
            converged = True
            break

    return {
        "kind": "weighted_binary_logistic",
        "intercept": float(weights[0]),
        "coefficients": [float(value) for value in weights[1:]],
        "iterations": iteration,
        "converged": converged,
    }


def _predict_binary_model(model: Mapping[str, Any], matrix: np.ndarray) -> np.ndarray:
    if model["kind"] == "constant":
        return np.full(matrix.shape[0], float(model["probability"]), dtype=float)
    coefficients = np.asarray(model["coefficients"], dtype=float)
    return _sigmoid(float(model["intercept"]) + matrix @ coefficients)


def _fit_resource_model(
    rows: Sequence[Mapping[str, Any]],
    resource: str,
    *,
    epsilon_pp: float,
    l2_penalty: float,
) -> Dict[str, Any]:
    candidates = [_candidate(row) for row in rows]
    encoder = FeatureEncoder.fit(candidates)
    matrix = encoder.transform(candidates)
    labels = np.asarray(
        [
            _direction_index(float(row["resource_delta_pp"][resource]), epsilon_pp)
            for row in rows
        ],
        dtype=int,
    )
    base_weights = _scope_weights(rows)
    class_models = [
        _fit_weighted_binary_logistic(
            matrix,
            (labels == class_index).astype(float),
            base_weights,
            l2_penalty=l2_penalty,
        )
        for class_index in range(len(DIRECTION_NAMES))
    ]
    return {
        "encoder": encoder,
        "class_models": class_models,
        "class_counts": {
            DIRECTION_NAMES[class_index]: int(np.sum(labels == class_index))
            for class_index in range(len(DIRECTION_NAMES))
        },
    }


def _predict_resource_model(
    model: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> Tuple[np.ndarray, np.ndarray]:
    encoder = model["encoder"]
    matrix = encoder.transform([_candidate(row) for row in rows])
    probabilities = np.column_stack(
        [
            _predict_binary_model(class_model, matrix)
            for class_model in model["class_models"]
        ]
    )
    row_sums = np.sum(probabilities, axis=1, keepdims=True)
    probabilities = probabilities / np.maximum(row_sums, 1e-12)
    return np.argmax(probabilities, axis=1), probabilities


def _balanced_accuracy(actual: np.ndarray, predicted: np.ndarray) -> float:
    recalls = []
    for class_index in sorted(set(int(value) for value in actual.tolist())):
        mask = actual == class_index
        recalls.append(float(np.mean(predicted[mask] == class_index)))
    return float(np.mean(recalls)) if recalls else 0.0


def _validation_score(actual: np.ndarray, predicted: np.ndarray) -> float:
    accuracy = float(np.mean(actual == predicted))
    balanced = _balanced_accuracy(actual, predicted)
    return 0.7 * accuracy + 0.3 * balanced


def _evaluate_l2(
    rows: Sequence[Mapping[str, Any]],
    resource: str,
    *,
    epsilon_pp: float,
    l2_penalty: float,
) -> Optional[Dict[str, float]]:
    scopes = sorted({str(row["incumbent_scope_id"]) for row in rows})
    if len(scopes) < 2:
        return None
    actual_all: List[int] = []
    predicted_all: List[int] = []
    for heldout_scope in scopes:
        train = [
            row for row in rows
            if str(row["incumbent_scope_id"]) != heldout_scope
        ]
        heldout = [
            row for row in rows
            if str(row["incumbent_scope_id"]) == heldout_scope
        ]
        if not train or not heldout:
            continue
        model = _fit_resource_model(
            train,
            resource,
            epsilon_pp=epsilon_pp,
            l2_penalty=l2_penalty,
        )
        predicted, _ = _predict_resource_model(model, heldout)
        actual = [
            _direction_index(float(row["resource_delta_pp"][resource]), epsilon_pp)
            for row in heldout
        ]
        actual_all.extend(actual)
        predicted_all.extend(int(value) for value in predicted)
    if not actual_all:
        return None
    actual_array = np.asarray(actual_all, dtype=int)
    predicted_array = np.asarray(predicted_all, dtype=int)
    return {
        "accuracy": float(np.mean(actual_array == predicted_array)),
        "balanced_accuracy": _balanced_accuracy(actual_array, predicted_array),
        "selection_score": _validation_score(actual_array, predicted_array),
    }


def _choose_l2(
    rows: Sequence[Mapping[str, Any]],
    resource: str,
    *,
    epsilon_pp: float,
) -> Tuple[float, List[Dict[str, Any]]]:
    evaluations: List[Dict[str, Any]] = []
    for l2_penalty in L2_GRID:
        metrics = _evaluate_l2(
            rows,
            resource,
            epsilon_pp=epsilon_pp,
            l2_penalty=l2_penalty,
        )
        if metrics is not None:
            evaluations.append({"l2_penalty": l2_penalty, **metrics})
    if not evaluations:
        return 1.0, evaluations
    best = max(
        evaluations,
        key=lambda item: (
            item["selection_score"],
            item["accuracy"],
            -item["l2_penalty"],
        ),
    )
    return float(best["l2_penalty"]), evaluations


def _nested_group_predictions(
    rows: Sequence[Mapping[str, Any]],
    resource: str,
    *,
    epsilon_pp: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    scopes = sorted({str(row["incumbent_scope_id"]) for row in rows})
    predictions: List[Dict[str, Any]] = []
    fold_audit: List[Dict[str, Any]] = []
    for heldout_scope in scopes:
        train = [
            row for row in rows
            if str(row["incumbent_scope_id"]) != heldout_scope
        ]
        heldout = [
            row for row in rows
            if str(row["incumbent_scope_id"]) == heldout_scope
        ]
        chosen_l2, tuning = _choose_l2(
            train,
            resource,
            epsilon_pp=epsilon_pp,
        )
        model = _fit_resource_model(
            train,
            resource,
            epsilon_pp=epsilon_pp,
            l2_penalty=chosen_l2,
        )
        predicted, probabilities = _predict_resource_model(model, heldout)
        for row, predicted_index, probability_row in zip(
            heldout,
            predicted,
            probabilities,
        ):
            actual_index = _direction_index(
                float(row["resource_delta_pp"][resource]),
                epsilon_pp,
            )
            predictions.append({
                "sample_id": row["sample_id"],
                "heldout_scope_id": heldout_scope,
                "actual": DIRECTION_NAMES[actual_index],
                "predicted": DIRECTION_NAMES[int(predicted_index)],
                "confidence": float(np.max(probability_row)),
                "probabilities": {
                    DIRECTION_NAMES[index]: float(probability_row[index])
                    for index in range(len(DIRECTION_NAMES))
                },
                "correct": int(predicted_index) == actual_index,
            })
        fold_audit.append({
            "heldout_scope_id": heldout_scope,
            "train_sample_count": len(train),
            "heldout_sample_count": len(heldout),
            "selected_l2_penalty": chosen_l2,
            "inner_group_validation": tuning,
        })
    return predictions, fold_audit


def _wilson_lower(successes: int, total: int, z: float = CONFIDENCE_Z) -> float:
    if total <= 0:
        return 0.0
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = proportion + z * z / (2.0 * total)
    radius = z * math.sqrt(
        proportion * (1.0 - proportion) / total
        + z * z / (4.0 * total * total)
    )
    return max(0.0, (center - radius) / denominator)


def _confusion(predictions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    result = {
        actual: {predicted: 0 for predicted in DIRECTION_NAMES}
        for actual in DIRECTION_NAMES
    }
    for row in predictions:
        result[str(row["actual"])][str(row["predicted"])] += 1
    return result


def _operating_point(
    predictions: Sequence[Mapping[str, Any]],
    *,
    min_accuracy: float,
    min_wilson_lower: float,
    min_coverage: float,
    min_known: int,
) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    total = len(predictions)
    for threshold in CONFIDENCE_THRESHOLDS:
        known = [
            row for row in predictions
            if float(row["confidence"]) >= threshold
        ]
        successes = sum(bool(row["correct"]) for row in known)
        count = len(known)
        accuracy = successes / count if count else 0.0
        coverage = count / total if total else 0.0
        wilson = _wilson_lower(successes, count)
        passed = (
            count >= min_known
            and coverage >= min_coverage
            and accuracy >= min_accuracy
            and wilson >= min_wilson_lower
        )
        candidates.append({
            "confidence_threshold": threshold,
            "known_count": count,
            "coverage": coverage,
            "accuracy": accuracy,
            "accuracy_wilson_lower_90": wilson,
            "passed": passed,
        })
    passing = [row for row in candidates if row["passed"]]
    selected = (
        max(
            passing,
            key=lambda row: (
                row["coverage"],
                row["accuracy_wilson_lower_90"],
                -row["confidence_threshold"],
            ),
        )
        if passing else None
    )
    return {
        "selected": selected,
        "evaluated_thresholds": candidates,
    }


def _support_domain(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    parameter_names = sorted({
        str(name)
        for row in rows
        for name in row["parameter_log_delta"]
    })
    parameter_bounds = {}
    for name in parameter_names:
        values = [
            _finite(row["parameter_log_delta"].get(name))
            for row in rows
        ]
        parameter_bounds[name] = {"min": min(values), "max": max(values)}
    incumbent_bounds = {}
    for resource in RESOURCES:
        values = [
            _finite(row["incumbent_utilization_pct"].get(resource))
            for row in rows
        ]
        incumbent_bounds[resource] = {"min": min(values), "max": max(values)}
    return {
        "parameter_log_delta_bounds": parameter_bounds,
        "incumbent_utilization_pct_bounds": incumbent_bounds,
        "allowed_phases": sorted({str(row["phase"]) for row in rows}),
        "allowed_evidence_kinds": sorted({
            str(row["evidence_kind"]) for row in rows
        }),
        "plan_evidence_sample_count": sum(
            bool(row["plan_evidence"].get("available")) for row in rows
        ),
    }


def _serialize_fitted_model(
    fitted: Mapping[str, Any],
    *,
    l2_penalty: float,
) -> Dict[str, Any]:
    return {
        "model_type": "scope_and_class_weighted_one_vs_rest_logistic_v1",
        "l2_penalty": l2_penalty,
        "encoder": asdict(fitted["encoder"]),
        "class_models": {
            DIRECTION_NAMES[index]: model
            for index, model in enumerate(fitted["class_models"])
        },
        "class_counts": fitted["class_counts"],
    }


def _train_group(
    rows: Sequence[Mapping[str, Any]],
    *,
    epsilon_pp: float,
    min_samples: int,
    min_scopes: int,
    min_accuracy: float,
    min_wilson_lower: float,
    min_coverage: float,
    min_known: int,
) -> Dict[str, Any]:
    first = rows[0]
    scope_count = len({str(row["incumbent_scope_id"]) for row in rows})
    result: Dict[str, Any] = {
        "isolation_key": {
            "workload": first["workload"],
            "scenario": first["scenario"],
            "target_resource": first["target_resource"],
            "capability": first["capability"],
        },
        "sample_count": len(rows),
        "scope_count": scope_count,
        "scope_sample_counts": dict(sorted(Counter(
            str(row["incumbent_scope_id"]) for row in rows
        ).items())),
        "support_domain": _support_domain(rows),
        "resources": {},
    }
    if len(rows) < min_samples or scope_count < min_scopes:
        result["group_ready"] = False
        result["group_failures"] = [
            *(
                [f"sample_count:{len(rows)}<{min_samples}"]
                if len(rows) < min_samples else []
            ),
            *(
                [f"scope_count:{scope_count}<{min_scopes}"]
                if scope_count < min_scopes else []
            ),
        ]
        return result

    for resource in RESOURCES:
        predictions, folds = _nested_group_predictions(
            rows,
            resource,
            epsilon_pp=epsilon_pp,
        )
        actual = np.asarray(
            [DIRECTION_NAMES.index(str(row["actual"])) for row in predictions],
            dtype=int,
        )
        predicted = np.asarray(
            [DIRECTION_NAMES.index(str(row["predicted"])) for row in predictions],
            dtype=int,
        )
        full_accuracy = float(np.mean(actual == predicted))
        balanced_accuracy = _balanced_accuracy(actual, predicted)
        operating = _operating_point(
            predictions,
            min_accuracy=min_accuracy,
            min_wilson_lower=min_wilson_lower,
            min_coverage=min_coverage,
            min_known=min_known,
        )
        selected_l2, final_tuning = _choose_l2(
            rows,
            resource,
            epsilon_pp=epsilon_pp,
        )
        fitted = _fit_resource_model(
            rows,
            resource,
            epsilon_pp=epsilon_pp,
            l2_penalty=selected_l2,
        )
        selected_operating = operating["selected"]
        ready = selected_operating is not None
        result["resources"][resource] = {
            "ready": ready,
            "full_coverage_accuracy": full_accuracy,
            "balanced_accuracy": balanced_accuracy,
            "full_coverage_correct_count": int(np.sum(actual == predicted)),
            "validation_sample_count": len(predictions),
            "confusion_matrix": _confusion(predictions),
            "operating_point": operating,
            "nested_scope_holdout_folds": folds,
            "final_hyperparameter_selection": final_tuning,
            "fitted_model": _serialize_fitted_model(
                fitted,
                l2_penalty=selected_l2,
            ),
            "runtime_policy": (
                "emit predicted direction only when probability is at least the "
                "selected threshold and the input remains inside support_domain; "
                "otherwise emit unknown"
            ),
        }
    result["group_ready"] = all(
        bool(result["resources"][resource]["ready"])
        for resource in RESOURCES
    )
    result["target_resource_ready"] = bool(
        result["resources"][str(first["target_resource"])]["ready"]
    )
    result["pruning_safe"] = result["group_ready"]
    return result


def _build_document(args: argparse.Namespace) -> Dict[str, Any]:
    audit = json.loads(Path(args.audit).read_text(encoding="utf-8"))
    fresh, fresh_rejected = _fresh_observations(audit)
    optimizer, optimizer_rejected = _optimizer_observations(audit)
    observations = fresh + optimizer
    grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in observations:
        grouped[(
            str(row["workload"]),
            str(row["scenario"]),
            _capability_token(row["capability"]),
        )].append(row)
    models = [
        _train_group(
            rows,
            epsilon_pp=args.epsilon_pp,
            min_samples=args.min_samples,
            min_scopes=args.min_scopes,
            min_accuracy=args.min_accuracy,
            min_wilson_lower=args.min_wilson_lower,
            min_coverage=args.min_coverage,
            min_known=args.min_known,
        )
        for _, rows in sorted(grouped.items())
    ]
    return {
        "artifact_type": "pg_scenario_resource_direction_model_v1",
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_audit": str(Path(args.audit).resolve()),
        "training_target": (
            "three-class signed utilization delta in percentage points: "
            "candidate minus incumbent"
        ),
        "qps_used": False,
        "isolation_policy": (
            "separate model per exact (workload, scenario, capability); "
            "no cross-capability pooling"
        ),
        "validation_policy": (
            "nested leave-one-incumbent-scope-out; hyperparameters selected "
            "inside each outer training fold"
        ),
        "direction_epsilon_pp": args.epsilon_pp,
        "readiness_gate": {
            "min_samples": args.min_samples,
            "min_scopes": args.min_scopes,
            "min_known_predictions": args.min_known,
            "min_known_coverage": args.min_coverage,
            "min_known_accuracy": args.min_accuracy,
            "min_accuracy_wilson_lower_90": args.min_wilson_lower,
            "all_three_resources_required_for_pruning_safe": True,
        },
        "extraction": {
            "admitted_observation_count": len(observations),
            "fresh_observation_count": len(fresh),
            "optimizer_observation_count": len(optimizer),
            "fresh_rejections": fresh_rejected,
            "optimizer_rejections": optimizer_rejected,
        },
        "summary": {
            "model_group_count": len(models),
            "groups_with_all_resources_ready": sum(
                bool(model.get("group_ready")) for model in models
            ),
            "groups_with_target_resource_ready": sum(
                bool(model.get("target_resource_ready")) for model in models
            ),
        },
        "models": models,
    }


def _write_report(document: Mapping[str, Any], path: Path) -> None:
    lines = [
        "# PostgreSQL ",
        "",
        f"- `{document['created_at_utc']}`",
        f"- `{document['extraction']['admitted_observation_count']}`",
        f"- `±{document['direction_epsilon_pp']}` ",
        "-  incumbent scope ",
        "- QPS",
        "",
        "## ",
        "",
        "| workload | scenario | capability | /scope |  |  |  | / | ready |",
        "|---|---|---|---:|---|---:|---:|---:|---|",
    ]
    for model in document["models"]:
        key = model["isolation_key"]
        capability = json.dumps(
            key["capability"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if not model.get("resources"):
            lines.append(
                f"| {key['workload']} | {key['scenario']} | `{capability}` | "
                f"{model['sample_count']}/{model['scope_count']} | — | — | — | — | false |"
            )
            continue
        for resource in RESOURCES:
            result = model["resources"][resource]
            selected = result["operating_point"]["selected"]
            selected_text = (
                f"{selected['coverage']:.1%}/{selected['accuracy']:.1%}"
                if selected else ""
            )
            lines.append(
                f"| {key['workload']} | {key['scenario']} | `{capability}` | "
                f"{model['sample_count']}/{model['scope_count']} | {resource} | "
                f"{result['full_coverage_accuracy']:.1%} | "
                f"{result['balanced_accuracy']:.1%} | {selected_text} | "
                f"{str(result['ready']).lower()} |"
            )
    lines.extend([
        "",
        "## ",
        "",
        "- `target_resource_ready=true` ",
        "- `pruning_safe=true`  CPUMemoryIO ",
        "- phase/evidence kind  `unknown`",
        "-  outer ",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", default=str(DEFAULT_AUDIT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--epsilon-pp", type=float, default=0.1)
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument("--min-scopes", type=int, default=3)
    parser.add_argument("--min-known", type=int, default=10)
    parser.add_argument("--min-coverage", type=float, default=0.20)
    parser.add_argument("--min-accuracy", type=float, default=0.80)
    parser.add_argument("--min-wilson-lower", type=float, default=0.60)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.epsilon_pp < 0.0:
        raise ValueError("--epsilon-pp cannot be negative")
    document = _build_document(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_report(document, Path(args.report))
    print(json.dumps({
        "output": str(output),
        "report": str(Path(args.report)),
        **document["summary"],
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
