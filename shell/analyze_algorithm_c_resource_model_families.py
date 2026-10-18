#!/usr/bin/env python3
"""Compare resource-only model families on a grouped Algorithm C holdout.

This is an offline diagnostic.  It never reads QPS, accept/reject labels, or
uses historical candidates to construct the Algorithm C runtime candidate set.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np
from sklearn.ensemble import (
    ExtraTreesRegressor,
    RandomForestRegressor,
)

from build_algorithm_c_experience_table_from_replays import (
    load_samples,
    prediction_metrics,
)


RESOURCES = ("cpu", "memory", "io")


def sample_group(sample: Mapping[str, Any]) -> str:
    return str(sample.get("holdout_group") or "")


def grouped_tail_split(
    samples: Sequence[Dict[str, Any]],
    *,
    holdout_count: int,
    validation_count: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    group_order: List[str] = []
    for sample in samples:
        group = sample_group(sample)
        if group not in groups:
            groups[group] = []
            group_order.append(group)
        groups[group].append(sample)

    def take_tail(
        available: Sequence[str], minimum_rows: int
    ) -> Tuple[List[str], List[str]]:
        selected: List[str] = []
        count = 0
        for group in reversed(available):
            if {
                str(row.get("training_source_algorithm") or "")
                for row in groups[group]
            } != {"algorithm_c"}:
                continue
            selected.append(group)
            count += len(groups[group])
            if count >= minimum_rows:
                break
        selected_set = set(selected)
        return selected, [group for group in available if group not in selected_set]

    holdout_groups, remaining = take_tail(group_order, holdout_count)
    validation_groups, remaining = take_tail(remaining, validation_count)
    holdout_set = set(holdout_groups)
    validation_set = set(validation_groups)
    fit = [
        row
        for row in samples
        if sample_group(row) not in holdout_set | validation_set
    ]
    validation = [row for row in samples if sample_group(row) in validation_set]
    holdout = [row for row in samples if sample_group(row) in holdout_set]
    if min(len(fit), len(validation), len(holdout)) <= 0:
        raise RuntimeError("grouped split is empty")
    return fit, validation, holdout


def rich_feature_schema(
    samples: Sequence[Mapping[str, Any]],
) -> Tuple[List[str], Dict[str, float]]:
    observed: Dict[str, List[float]] = {}
    for sample in samples:
        plan = sample.get("plan_prediction") or {}
        current = plan.get("current_features") or {}
        if not isinstance(current, Mapping):
            continue
        for resource in RESOURCES:
            values = current.get(resource) or {}
            if not isinstance(values, Mapping):
                continue
            for name, value in values.items():
                if isinstance(value, (int, float)) and math.isfinite(float(value)):
                    transformed = math.copysign(math.log1p(abs(float(value))), float(value))
                    observed.setdefault(f"{resource}:{name}", []).append(transformed)
    minimum_count = max(10, int(math.ceil(len(samples) * 0.50)))
    names = sorted(
        name
        for name, values in observed.items()
        if len(values) >= minimum_count and float(np.std(values)) > 1e-9
    )
    medians = {
        name: float(np.median(np.asarray(observed[name], dtype=float)))
        for name in names
    }
    return names, medians


def feature_matrix(
    samples: Sequence[Mapping[str, Any]],
    rich_names: Sequence[str],
    rich_medians: Mapping[str, float],
) -> np.ndarray:
    rows: List[List[float]] = []
    for sample in samples:
        plan = sample.get("plan_prediction") or {}
        ratios = plan.get("predicted_log_ratios") or {}
        current = plan.get("current_features") or {}
        flat: Dict[str, float] = {}
        if isinstance(current, Mapping):
            for resource in RESOURCES:
                values = current.get(resource) or {}
                if not isinstance(values, Mapping):
                    continue
                for name, value in values.items():
                    if isinstance(value, (int, float)) and math.isfinite(float(value)):
                        flat[f"{resource}:{name}"] = math.copysign(
                            math.log1p(abs(float(value))), float(value)
                        )
        rows.append([
            *[float(value) for value in sample["x"]],
            *[float(ratios[resource]) for resource in RESOURCES],
            *[float(flat.get(name, rich_medians[name])) for name in rich_names],
        ])
    return np.asarray(rows, dtype=float)


def group_weights(samples: Sequence[Mapping[str, Any]]) -> np.ndarray:
    counts: Dict[str, int] = {}
    for sample in samples:
        group = sample_group(sample)
        counts[group] = counts.get(group, 0) + 1
    row_count = len(samples)
    group_count = len(counts)
    return np.asarray(
        [
            row_count / (group_count * counts[sample_group(sample)])
            for sample in samples
        ],
        dtype=float,
    )


def model_candidates() -> List[Tuple[str, Any]]:
    candidates: List[Tuple[str, Any]] = []
    for leaf in (1, 4, 16):
        candidates.append(
            (
                f"extra_trees_leaf_{leaf}",
                ExtraTreesRegressor(
                    n_estimators=40,
                    min_samples_leaf=leaf,
                    max_features=1.0,
                    random_state=1729,
                    n_jobs=1,
                ),
            )
        )
        candidates.append(
            (
                f"random_forest_leaf_{leaf}",
                RandomForestRegressor(
                    n_estimators=40,
                    min_samples_leaf=leaf,
                    max_features=1.0,
                    random_state=1729,
                    n_jobs=1,
                ),
            )
        )
    return candidates


def selection_key(metrics: Mapping[str, Any]) -> Tuple[float, ...]:
    direction = metrics.get("direction") or {}
    spearman = metrics.get("spearman")
    rank = float(spearman) if spearman is not None else -1.0
    precision = float(direction.get("point_predicted_target_down_precision") or 0.0)
    confirmed = int(direction.get("point_confirmed_target_down_count") or 0)
    return (
        0.0 if rank >= 0.50 else 1.0,
        0.0 if precision >= 0.80 and confirmed >= 2 else 1.0,
        -rank,
        float(metrics.get("mae_log_ratio") or math.inf),
        -precision,
        -float(confirmed),
    )


def training_scopes(
    samples: Sequence[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    def recent_groups(
        rows: Sequence[Dict[str, Any]], count: int
    ) -> List[Dict[str, Any]]:
        ordered = list(dict.fromkeys(sample_group(row) for row in rows))
        selected = set(ordered[-count:])
        return [row for row in rows if sample_group(row) in selected]

    c_rows = [
        row
        for row in samples
        if str(row.get("training_source_algorithm") or "") == "algorithm_c"
    ]
    scopes = {
        "all_abc": list(samples),
        "algorithm_c_only": c_rows,
        "recent_c_10_groups": recent_groups(c_rows, 10),
        "recent_c_20_groups": recent_groups(c_rows, 20),
        "recent_c_40_groups": recent_groups(c_rows, 40),
        "recent_all_20_groups": recent_groups(samples, 20),
    }
    return {
        name: rows
        for name, rows in scopes.items()
        if len(rows) >= 20
    }


def evaluate(
    fit: Sequence[Dict[str, Any]],
    validation: Sequence[Dict[str, Any]],
    holdout: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    rich_names, rich_medians = rich_feature_schema(fit)
    x_fit = feature_matrix(fit, rich_names, rich_medians)
    x_validation = feature_matrix(validation, rich_names, rich_medians)
    x_holdout = feature_matrix(holdout, rich_names, rich_medians)
    result: Dict[str, Any] = {}
    for resource in RESOURCES:
        y_validation = np.asarray(
            [row["outcomes"][resource] for row in validation], dtype=float
        )
        y_holdout = np.asarray(
            [row["outcomes"][resource] for row in holdout], dtype=float
        )
        audits: List[Dict[str, Any]] = []
        for scope_name, scope_rows in training_scopes(fit).items():
            scope_x = feature_matrix(scope_rows, rich_names, rich_medians)
            scope_y = np.asarray(
                [row["outcomes"][resource] for row in scope_rows], dtype=float
            )
            for name, model in model_candidates():
                model.fit(
                    scope_x,
                    scope_y,
                    sample_weight=group_weights(scope_rows),
                )
                predicted = model.predict(x_validation)
                metrics = prediction_metrics(y_validation, predicted)
                audits.append(
                    {
                        "name": name,
                        "training_scope": scope_name,
                        "training_count": len(scope_rows),
                        "metrics": metrics,
                        "key": list(selection_key(metrics)),
                    }
                )
        selected = min(audits, key=lambda row: tuple(row["key"]))
        selected_name = str(selected["name"])
        selected_scope = str(selected["training_scope"])
        final_model = dict(model_candidates())[selected_name]
        combined = [*fit, *validation]
        final_rows = training_scopes(combined)[selected_scope]
        x_combined = feature_matrix(final_rows, rich_names, rich_medians)
        y_combined = np.asarray(
            [row["outcomes"][resource] for row in final_rows], dtype=float
        )
        final_model.fit(
            x_combined,
            y_combined,
            sample_weight=group_weights(final_rows),
        )
        holdout_metrics = prediction_metrics(
            y_holdout,
            final_model.predict(x_holdout),
        )
        result[resource] = {
            "selected": selected_name,
            "selected_training_scope": selected_scope,
            "selected_training_count": len(final_rows),
            "validation": selected["metrics"],
            "holdout": holdout_metrics,
            "candidate_audit": audits,
        }
    result["_feature_audit"] = {
        "base_feature_count": 10,
        "rich_pre_replay_feature_count": len(rich_names),
        "rich_pre_replay_features": rich_names,
        "qps_used": False,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="append", required=True, type=Path)
    parser.add_argument("--source-algorithm", action="append", required=True)
    parser.add_argument("--active-params", required=True)
    parser.add_argument("--holdout-count", type=int, default=30)
    parser.add_argument("--validation-count", type=int, default=30)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if len(args.history) != len(args.source_algorithm):
        raise ValueError("history/source algorithm counts differ")
    active_params = [value.strip() for value in args.active_params.split(",")]
    samples, audit = load_samples(
        args.history,
        active_params,
        source_algorithms={
            str(path): algorithm
            for path, algorithm in zip(args.history, args.source_algorithm)
        },
    )
    samples = [
        sample
        for sample in samples
        if isinstance((sample.get("plan_prediction") or {}).get("predicted_log_ratios"), Mapping)
    ]
    fit, validation, holdout = grouped_tail_split(
        samples,
        holdout_count=max(10, args.holdout_count),
        validation_count=max(10, args.validation_count),
    )
    payload = {
        "contract": "algorithm_c_resource_model_family_diagnostic_v1",
        "qps_used": False,
        "accept_reject_used": False,
        "runtime_candidate_set_constructed_from_training": False,
        "fit_count": len(fit),
        "validation_count": len(validation),
        "holdout_count": len(holdout),
        "holdout_rows": [
            {
                "label": row.get("label"),
                "group": sample_group(row),
                "round_index": int(row.get("round_index") or 0),
            }
            for row in holdout
        ],
        "source_audit": audit,
        "resources": evaluate(fit, validation, holdout),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(str(args.output), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
