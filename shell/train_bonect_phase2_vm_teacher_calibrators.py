#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SHELL_DIR = ROOT / "shell"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SHELL_DIR) not in sys.path:
    sys.path.insert(0, str(SHELL_DIR))

from phase2_delta_calibrator import Phase2DeltaCalibrator  # noqa: E402
from run_bonect_phase2_vm_teacher_validation import (  # noqa: E402
    COST_PARAM_KEYS,
    SCENARIO_ORDER,
    normalized_params,
    read_json,
)
from train_bonect_phase2_none_delta_calibrator import (  # noqa: E402
    TARGETS,
    direction_accuracy,
    loo_predictions,
    nearest_neighbor_distances,
    pairwise_inversion_rate,
    safe_float,
    select_plan_columns,
)


DEFAULT_CAMPAIGN_DIR = (
    ROOT / "report" / "bonect" / "phase2_vm_teacher_neighborhood_v80_20260711"
)
DEFAULT_TEACHER_DIR = (
    ROOT / "report" / "bonect" / "phase2_vm_teacher_validation_v79_20260711"
)


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def mean_maps(records: Sequence[Mapping[str, Any]], key: str) -> Dict[str, float]:
    names = sorted(
        set().union(
            *(
                set((record.get(key) or {}).keys())
                for record in records
                if isinstance(record.get(key), Mapping)
            )
        )
    )
    result: Dict[str, float] = {}
    for name in names:
        values = [
            safe_float((record.get(key) or {}).get(name), float("nan"))
            for record in records
        ]
        values = [value for value in values if math.isfinite(value)]
        if values:
            result[name] = float(np.mean(values))
    return result


def delta_pct(value: float, baseline: float) -> float:
    return 100.0 * (value - baseline) / baseline if abs(baseline) > 1e-12 else 0.0


def params_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(
        abs(safe_float(left.get(name)) - safe_float(right.get(name))) <= 1e-10
        for name in COST_PARAM_KEYS
    )


def build_blind_deployment_artifact(
    audit_artifact: Mapping[str, Any],
) -> Dict[str, Any]:
    """Strip answer-bearing coordinates while preserving the trained predictor."""
    deployment = copy.deepcopy(dict(audit_artifact))
    deployment.pop("teacher_holdout", None)
    for point in deployment.get("points") or []:
        if isinstance(point, dict):
            point.pop("params", None)
    for evaluation in deployment.get("bandwidth_evaluations") or []:
        if isinstance(evaluation, dict):
            evaluation.pop("loo_predictions", None)
    deployment["blind_deployment"] = {
        "selection_blind": True,
        "exact_teacher_coordinates_present": False,
        "teacher_holdout_removed": True,
        "training_point_parameter_coordinates_removed": True,
        "baseline_params_are_postgresql_defaults": True,
        "note": (
            "The model retains measured feature vectors and labels from teacher-neighborhood "
            "sampling, but no exact teacher or training-point parameter coordinates."
        ),
    }
    if "teacher_holdout" in deployment:
        raise RuntimeError("blind deployment artifact still contains teacher_holdout")
    coordinate_points = [
        point.get("point_name")
        for point in deployment.get("points") or []
        if isinstance(point, Mapping) and "params" in point
    ]
    if coordinate_points:
        raise RuntimeError(
            "blind deployment artifact still contains training coordinates: "
            f"{coordinate_points}"
        )
    return deployment


def load_training_records(
    state_path: Path,
    *,
    scenario: str,
    default_params: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    baselines: Dict[int, Dict[str, Any]] = {}
    candidates: Dict[int, Dict[str, Any]] = {}
    for record in iter_jsonl(state_path):
        if str(record.get("scenario") or "") != scenario:
            continue
        event = str(record.get("event") or "")
        try:
            block_index = int(record.get("block_index"))
        except (TypeError, ValueError):
            block_index = -1
        if event == "teacher_neighborhood_baseline_complete":
            baselines[block_index] = record
        elif event == "teacher_neighborhood_point_complete":
            try:
                point_index = int(record.get("point_index"))
            except (TypeError, ValueError):
                continue
            candidates[point_index] = record
    records: List[Dict[str, Any]] = []
    for block_index, baseline in sorted(baselines.items()):
        if not (baseline.get("measurement_gate") or {}).get("passed"):
            continue
        records.append(
            {
                **baseline,
                "point_index": f"baseline_{block_index}",
                "point_name": f"paired_default_block_{block_index}",
                "params": normalized_params(default_params),
                "paired_baseline_valid": True,
                "paired_baseline": baseline,
                "training_role": "paired_default_zero_delta",
            }
        )
    for point_index, record in sorted(candidates.items()):
        if not record.get("paired_baseline_valid"):
            continue
        if not (record.get("measurement_gate") or {}).get("passed"):
            continue
        if not record.get("plan_features"):
            continue
        records.append(
            {
                **record,
                "point_index": point_index,
                "point_name": record.get("name"),
                "training_role": "teacher_neighbor",
            }
        )
    return records


def labels_for_record(record: Mapping[str, Any]) -> List[float]:
    baseline = record.get("paired_baseline") or {}
    baseline_resource = baseline.get("resource_normalized") or {}
    resource = record.get("resource_normalized") or {}
    return [
        delta_pct(
            safe_float(record.get("db_qps")),
            safe_float(baseline.get("db_qps")),
        ),
        *[
            delta_pct(
                safe_float(resource.get(name)),
                safe_float(baseline_resource.get(name)),
            )
            for name in ("cpu", "memory", "io")
        ],
    ]


def load_teacher_points(state_path: Path, scenario: str) -> Dict[str, Dict[str, Any]]:
    points: Dict[str, Dict[str, Any]] = {}
    for record in iter_jsonl(state_path):
        if record.get("event") != "teacher_validation_point_complete":
            continue
        if str(record.get("scenario") or "") != scenario:
            continue
        role = str(record.get("role") or "")
        if role in {"default_before", "teacher", "default_after"}:
            points[role] = record
    return points


def scenario_direction_pass(
    scenario: str,
    prediction: Mapping[str, Any],
    *,
    min_db_gain_pct: float,
) -> Tuple[bool, Dict[str, Any]]:
    db_gain = safe_float(prediction.get("db_qps_delta_pct"), float("nan"))
    deltas = {
        name: safe_float(prediction.get(f"{name}_delta_pct"), float("nan"))
        for name in ("cpu", "memory", "io")
    }
    db_ok = math.isfinite(db_gain) and db_gain >= min_db_gain_pct
    if scenario == "none":
        direction_ok = any(
            math.isfinite(value) and value > 0.0 for value in deltas.values()
        )
        detail = {
            "db_gain_pct": db_gain,
            "db_gain_passed": db_ok,
            "resource_delta_pct": deltas,
            "any_resource_up": direction_ok,
        }
    else:
        target_down = math.isfinite(deltas[scenario]) and deltas[scenario] < 0.0
        other_up = [
            name
            for name, value in deltas.items()
            if name != scenario and math.isfinite(value) and value > 0.0
        ]
        direction_ok = target_down and bool(other_up)
        detail = {
            "db_gain_pct": db_gain,
            "db_gain_passed": db_ok,
            "resource_delta_pct": deltas,
            "target_down": target_down,
            "other_resources_up": other_up,
        }
    return db_ok and direction_ok, detail


def train_scenario(
    *,
    scenario: str,
    campaign_dir: Path,
    teacher_dir: Path,
    teacher_manifest: Mapping[str, Any],
    teacher_summary: Mapping[str, Any],
    min_samples: int,
    max_plan_features: int,
) -> Dict[str, Any]:
    default_params = normalized_params(teacher_manifest.get("default_params") or {})
    teacher_params = normalized_params(
        (teacher_manifest.get("scenarios") or {})[scenario].get("teacher_params") or {}
    )
    records = load_training_records(
        campaign_dir / "state.jsonl",
        scenario=scenario,
        default_params=default_params,
    )
    if len(records) < min_samples:
        raise RuntimeError(
            f"{scenario}: insufficient neighborhood samples: {len(records)} < {min_samples}"
        )
    leaked = [
        record.get("point_name")
        for record in records
        if params_equal(record.get("params") or {}, teacher_params)
    ]
    if leaked:
        raise RuntimeError(f"{scenario}: exact teacher leaked into training: {leaked}")

    baseline_records = [
        record for record in records if record.get("training_role") == "paired_default_zero_delta"
    ]
    if not baseline_records:
        raise RuntimeError(f"{scenario}: no paired defaults available")
    baseline_plan = mean_maps(baseline_records, "plan_features")
    baseline_resource = mean_maps(baseline_records, "resource_normalized")
    baseline_db_qps = float(
        np.mean([safe_float(record.get("db_qps")) for record in baseline_records])
    )

    param_rows = [
        [
            math.log(
                max(safe_float((record.get("params") or {}).get(name)), 1e-12)
                / max(default_params[name], 1e-12)
            )
            for name in COST_PARAM_KEYS
        ]
        for record in records
    ]
    labels = [labels_for_record(record) for record in records]
    y = np.asarray(labels, dtype=float)
    plan_columns = select_plan_columns(
        records,
        baseline_plan,
        max(0, max_plan_features),
    )
    raw_columns: List[np.ndarray] = []
    feature_names: List[str] = []
    for index, name in enumerate(COST_PARAM_KEYS):
        values = np.asarray([row[index] for row in param_rows], dtype=float)
        if float(np.std(values)) <= 1e-12:
            continue
        raw_columns.append(values)
        feature_names.append(f"param_log:{name}")
    for name, values, _metadata in plan_columns:
        raw_columns.append(values)
        feature_names.append(f"plan_delta:{name}")
    if not raw_columns:
        raise RuntimeError(f"{scenario}: no varying model features")
    raw_x = np.column_stack(raw_columns)
    scales = np.maximum(np.std(raw_x, axis=0), 1e-12)
    x = raw_x / scales[None, :]

    evaluations: List[Dict[str, Any]] = []
    for bandwidth in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
        predicted = loo_predictions(x, y, bandwidth)
        inversion = pairwise_inversion_rate(y[:, 0], predicted[:, 0])
        direction = {
            TARGETS[index]: direction_accuracy(y[:, index], predicted[:, index])
            for index in range(1, len(TARGETS))
        }
        normalized_mae = float(
            np.mean(np.abs(predicted - y) / np.maximum(np.std(y, axis=0), 1.0))
        )
        objective = (
            4.0 * inversion
            + float(np.mean([1.0 - value for value in direction.values()]))
            + 0.05 * normalized_mae
        )
        evaluations.append(
            {
                "bandwidth": bandwidth,
                "objective": objective,
                "pairwise_inversion_rate": inversion,
                "direction_accuracy": direction,
                "normalized_mae": normalized_mae,
                "loo_predictions": predicted.tolist(),
            }
        )
    selected = min(evaluations, key=lambda item: item["objective"])
    neighbor_distances = nearest_neighbor_distances(x)
    neighbor_p95 = (
        float(np.quantile(neighbor_distances, 0.95))
        if len(neighbor_distances)
        else 0.0
    )
    points = [
        {
            "point_index": record.get("point_index"),
            "point_name": record.get("point_name"),
            "training_role": record.get("training_role"),
            "params": record.get("params") or {},
            "x": vector,
            "paired_baseline_valid": bool(record.get("paired_baseline_valid")),
            "labels": {name: value for name, value in zip(TARGETS, label)},
        }
        for record, vector, label in zip(records, x.tolist(), labels)
    ]
    artifact: Dict[str, Any] = {
        "version": "phase2_vm_teacher_neighborhood_rbf_v1",
        "scenario": scenario,
        "campaign_id": read_json(campaign_dir / "manifest.json").get("campaign_id"),
        "sample_count": len(records),
        "teacher_exact_point_excluded": True,
        "feature_names": feature_names,
        "feature_scales": scales.tolist(),
        "plan_feature_selection": {
            "method": "target_independent_semantic_variation_v1",
            "selected": [
                {"name": name, **metadata}
                for name, _values, metadata in plan_columns
            ],
            "target_labels_used_for_selection": False,
        },
        "baseline_params": default_params,
        "baseline_plan_features": baseline_plan,
        "baseline_resource": baseline_resource,
        "baseline_db_qps": baseline_db_qps,
        "bandwidth": selected["bandwidth"],
        "support": {
            "method": "training_nearest_neighbor_p95_x_1.5",
            "training_nearest_distance_p95": neighbor_p95,
            "max_nearest_distance": max(1e-6, 1.5 * neighbor_p95),
            "training_nearest_distances": neighbor_distances.tolist(),
        },
        "validation": selected,
        "bandwidth_evaluations": evaluations,
        "training_gate": {"passed": True, "reasons": ["pending_teacher_holdout"]},
        "points": points,
    }

    teacher_points = load_teacher_points(teacher_dir / "state.jsonl", scenario)
    if set(teacher_points) != {"default_before", "teacher", "default_after"}:
        raise RuntimeError(f"{scenario}: incomplete teacher holdout records")
    teacher_record = teacher_points["teacher"]
    holdout_baselines = [
        teacher_points["default_before"],
        teacher_points["default_after"],
    ]
    holdout_plan_baseline = mean_maps(holdout_baselines, "plan_features")
    holdout_resource_baseline = mean_maps(holdout_baselines, "resource_normalized")
    calibrator = Phase2DeltaCalibrator(artifact)
    holdout_prediction = calibrator.predict(
        params=teacher_params,
        plan_features=teacher_record.get("plan_features") or {},
        baseline_resource=holdout_resource_baseline,
        baseline_plan_features=holdout_plan_baseline,
    )
    predicted_values = holdout_prediction.get("prediction") or {}
    direction_passed, direction_detail = scenario_direction_pass(
        scenario,
        predicted_values,
        min_db_gain_pct=15.0,
    )
    actual_evaluation = (teacher_summary.get("evaluations") or {}).get(scenario) or {}
    reasons: List[str] = []
    if selected["pairwise_inversion_rate"] > 0.10:
        reasons.append("db_qps_pairwise_inversion_above_0.10")
    direction_values = list((selected.get("direction_accuracy") or {}).values())
    if direction_values and min(direction_values) < 0.70:
        reasons.append("resource_direction_accuracy_below_0.70")
    if actual_evaluation.get("strict_passed") is not True:
        reasons.append("vm_teacher_holdout_actual_strict_gate_failed")
    if not holdout_prediction.get("within_support"):
        reasons.append("vm_teacher_holdout_outside_model_support")
    if safe_float(holdout_prediction.get("plan_feature_coverage")) < 0.80:
        reasons.append("vm_teacher_holdout_plan_feature_coverage_below_0.80")
    if not direction_passed:
        reasons.append("vm_teacher_holdout_predicted_goal_failed")
    artifact["teacher_holdout"] = {
        "params": teacher_params,
        "actual_evaluation": actual_evaluation,
        "prediction": holdout_prediction,
        "predicted_goal": direction_detail,
        "predicted_goal_passed": direction_passed,
        "teacher_in_training": False,
    }
    artifact["training_gate"] = {
        "passed": not reasons,
        "reasons": reasons,
        "requirements": {
            "db_qps_pairwise_inversion_max": 0.10,
            "resource_direction_accuracy_min": 0.70,
            "teacher_holdout_plan_feature_coverage_min": 0.80,
            "teacher_holdout_predicted_db_qps_gain_min_pct": 15.0,
        },
    }
    return artifact


def write_report(output_dir: Path, artifacts: Mapping[str, Mapping[str, Any]]) -> Path:
    lines = [
        "# Phase2 VM teacher ",
        "",
        " teacher teacher  holdout",
        "",
        "|  |  | DB QPS LOO inversion |  | teacher  DB  | teacher  |  |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario in SCENARIO_ORDER:
        artifact = artifacts.get(scenario)
        if not artifact:
            lines.append(f"| {scenario} | - | - | - | - | - |  |")
            continue
        validation = artifact.get("validation") or {}
        directions = list((validation.get("direction_accuracy") or {}).values())
        holdout = artifact.get("teacher_holdout") or {}
        prediction = (holdout.get("prediction") or {}).get("prediction") or {}
        lines.append(
            "| {scenario} | {samples} | {inversion:.4f} | {direction:.4f} | {db_gain:+.3f}% | {holdout_pass} | {passed} |".format(
                scenario=scenario,
                samples=int(artifact.get("sample_count") or 0),
                inversion=float(validation.get("pairwise_inversion_rate") or 0.0),
                direction=min(directions) if directions else 0.0,
                db_gain=float(prediction.get("db_qps_delta_pct") or 0.0),
                holdout_pass="" if holdout.get("predicted_goal_passed") else "",
                passed="" if (artifact.get("training_gate") or {}).get("passed") else "",
            )
        )
    lines.extend(
        [
            "",
            " estimator-only  top-15 /",
            "",
        ]
    )
    path = output_dir / "estimator_training_gate.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Train four VM teacher-neighborhood delta calibrators.")
    parser.add_argument("--campaign-dir", default=str(DEFAULT_CAMPAIGN_DIR))
    parser.add_argument("--teacher-dir", default=str(DEFAULT_TEACHER_DIR))
    parser.add_argument("--min-samples", type=int, default=10)
    parser.add_argument("--max-plan-features", type=int, default=16)
    parser.add_argument("--scenario", action="append", choices=[*SCENARIO_ORDER, "all"])
    args = parser.parse_args()

    campaign_dir = Path(args.campaign_dir).expanduser().resolve()
    teacher_dir = Path(args.teacher_dir).expanduser().resolve()
    campaign_summary = read_json(campaign_dir / "summary.json")
    if campaign_summary.get("all_complete") is not True:
        raise RuntimeError("teacher neighborhood campaign is not complete")
    teacher_summary = read_json(teacher_dir / "summary.json")
    if teacher_summary.get("all_teachers_strict_passed") is not True:
        raise RuntimeError("four VM teacher validations have not all strictly passed")
    campaign_manifest = read_json(campaign_dir / "manifest.json")
    teacher_manifest = read_json(
        Path(campaign_manifest["resolved_teacher_validation_manifest"])
    )
    requested = args.scenario or ["all"]
    selected_scenarios = list(SCENARIO_ORDER) if "all" in requested else [
        scenario for scenario in SCENARIO_ORDER if scenario in requested
    ]
    artifacts: Dict[str, Dict[str, Any]] = {}
    blind_artifacts: Dict[str, Dict[str, Any]] = {}
    for scenario in selected_scenarios:
        artifact = train_scenario(
            scenario=scenario,
            campaign_dir=campaign_dir,
            teacher_dir=teacher_dir,
            teacher_manifest=teacher_manifest,
            teacher_summary=teacher_summary,
            min_samples=max(3, args.min_samples),
            max_plan_features=max(0, args.max_plan_features),
        )
        path = campaign_dir / f"{scenario}_delta_calibrator.json"
        path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        blind_artifact = build_blind_deployment_artifact(artifact)
        blind_path = campaign_dir / f"{scenario}_delta_calibrator_blind.json"
        blind_path.write_text(
            json.dumps(blind_artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        artifacts[scenario] = artifact
        blind_artifacts[scenario] = blind_artifact
        print(
            json.dumps(
                {
                    "scenario": scenario,
                    "audit_artifact": str(path),
                    "blind_artifact": str(blind_path),
                    "training_gate": artifact["training_gate"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    report_path = write_report(campaign_dir, artifacts)
    all_passed = (
        len(artifacts) == len(selected_scenarios)
        and all((artifact.get("training_gate") or {}).get("passed") for artifact in artifacts.values())
    )
    summary = {
        "all_passed": all_passed,
        "selected_scenarios": selected_scenarios,
        "artifacts": {
            scenario: str(campaign_dir / f"{scenario}_delta_calibrator.json")
            for scenario in artifacts
        },
        "audit_artifacts": {
            scenario: str(campaign_dir / f"{scenario}_delta_calibrator.json")
            for scenario in artifacts
        },
        "blind_artifacts": {
            scenario: str(campaign_dir / f"{scenario}_delta_calibrator_blind.json")
            for scenario in blind_artifacts
        },
        "report": str(report_path),
        "training_gates": {
            scenario: artifact.get("training_gate")
            for scenario, artifact in artifacts.items()
        },
    }
    (campaign_dir / "estimator_training_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if all_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
