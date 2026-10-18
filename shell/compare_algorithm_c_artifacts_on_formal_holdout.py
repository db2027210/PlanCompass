#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


ANCHOR_CONTRACT = "algorithm_c_formal_replay_local_incumbent_anchor_v1"
REPORT_CONTRACT = "algorithm_c_artifact_formal_holdout_comparison_v1"
RESOURCE_NAMES = ("cpu", "memory", "io")


class ValidationError(ValueError):
    """ValidationError."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json(path: Path) -> Tuple[Dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"artifact must be a JSON object: {path}")
    return value, _sha256_bytes(raw)


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValidationError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValidationError(f"anchor row must be an object at {path}:{line_number}")
            row = dict(row)
            row["_source_line"] = line_number
            rows.append(row)
    if not rows:
        raise ValidationError(f"anchor JSONL is empty: {path}")
    return rows


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValidationError(f"{field} must be numeric, got boolean")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValidationError(f"{field} must be finite")
    return number


def _artifact_model(artifact: Mapping[str, Any], resource: str) -> Tuple[List[str], Dict[str, float], float]:
    active_params_raw = artifact.get("active_params")
    if not isinstance(active_params_raw, list) or not active_params_raw:
        raise ValidationError("artifact.active_params must be a non-empty list")
    active_params = [str(item) for item in active_params_raw]
    if len(active_params) != len(set(active_params)):
        raise ValidationError("artifact.active_params contains duplicates")
    if any("qps" in name.lower() for name in active_params):
        raise ValidationError("QPS is forbidden in artifact.active_params")

    calibration = artifact.get("resource_prediction_calibration")
    if not isinstance(calibration, dict):
        raise ValidationError("artifact.resource_prediction_calibration is missing")
    if calibration.get("qps_used") is not False:
        raise ValidationError("artifact resource calibration must declare qps_used=false")
    calibration_params = calibration.get("active_params")
    if calibration_params is not None and list(calibration_params) != active_params:
        raise ValidationError("artifact and resource calibration active_params differ")
    resources = calibration.get("resources")
    if not isinstance(resources, dict) or resource not in resources:
        raise ValidationError(f"artifact has no calibration model for {resource}")
    model = resources[resource]
    if not isinstance(model, dict):
        raise ValidationError(f"artifact calibration model for {resource} is invalid")
    if model.get("qps_used") is not False:
        raise ValidationError(f"artifact {resource} model must declare qps_used=false")
    if model.get("zero_intercept") is not True:
        raise ValidationError(f"artifact {resource} model must declare zero_intercept=true")

    raw_coefficients = model.get("parameter_coefficients")
    coefficients: Dict[str, float]
    if isinstance(raw_coefficients, dict):
        if set(raw_coefficients) != set(active_params):
            raise ValidationError(f"artifact {resource} parameter coefficient keys differ from active_params")
        coefficients = {
            name: _finite_number(raw_coefficients[name], f"{resource}.parameter_coefficients.{name}")
            for name in active_params
        }
    elif isinstance(raw_coefficients, list) and len(raw_coefficients) == len(active_params):
        coefficients = {
            name: _finite_number(value, f"{resource}.parameter_coefficients[{index}]")
            for index, (name, value) in enumerate(zip(active_params, raw_coefficients))
        }
    else:
        raise ValidationError(f"artifact {resource} parameter_coefficients do not match active_params")
    plan_coefficient = _finite_number(
        model.get("plan_log_ratio_coefficient"),
        f"{resource}.plan_log_ratio_coefficient",
    )
    return active_params, coefficients, plan_coefficient


def _scope_signature(artifact: Mapping[str, Any]) -> Tuple[Dict[str, Any], str]:
    scope = artifact.get("scope")
    if not isinstance(scope, dict) or not scope:
        raise ValidationError("artifact.scope must be a non-empty object")
    return scope, _sha256_bytes(_canonical_json(scope))


def _validate_artifact_pair(old: Mapping[str, Any], new: Mapping[str, Any]) -> Dict[str, Any]:
    old_scope, old_scope_sha = _scope_signature(old)
    new_scope, new_scope_sha = _scope_signature(new)
    if old_scope != new_scope:
        raise ValidationError("old/new artifact workload contract scopes differ")
    for field in ("benchmark", "target_resource"):
        if old.get(field) != new.get(field):
            raise ValidationError(f"old/new artifact {field} differs")
    if old.get("active_params") != new.get("active_params"):
        raise ValidationError("old/new artifact active_params differ")
    old_resources = set((old.get("resource_prediction_calibration") or {}).get("resources") or {})
    new_resources = set((new.get("resource_prediction_calibration") or {}).get("resources") or {})
    if old_resources != new_resources:
        raise ValidationError("old/new artifact calibrated resource sets differ")
    missing = set(RESOURCE_NAMES) - old_resources
    if missing:
        raise ValidationError(f"artifact pair is missing required resources: {sorted(missing)}")
    for resource in RESOURCE_NAMES:
        _artifact_model(old, resource)
        _artifact_model(new, resource)
    return {
        "workload_scope_equal": True,
        "workload_scope_sha256": old_scope_sha,
        "benchmark": old.get("benchmark"),
        "target_resource": old.get("target_resource"),
        "active_params": list(old.get("active_params") or []),
        "resources": list(RESOURCE_NAMES),
        "qps_used_for_resource_prediction": False,
        "new_scope_sha256": new_scope_sha,
    }


def _candidate_ordinal(label: str) -> Optional[int]:
    match = re.search(r"_c(\d+)(?:_|$)", label)
    return int(match.group(1)) if match else None


def _anchor_group_order(group: str, rows: Sequence[Mapping[str, Any]]) -> Tuple[int, str]:
    rounds = []
    for row in rows:
        anchor = row.get("formal_anchor") or {}
        value = anchor.get("original_round_index")
        if value is not None:
            rounds.append(int(value))
    suffix = re.search(r"(\d+)$", group)
    return (max(rounds) if rounds else (int(suffix.group(1)) if suffix else -1), group)


def _anchor_row_errors(
    row: Mapping[str, Any],
    expected_contract: str,
    artifact: Mapping[str, Any],
) -> List[str]:
    errors: List[str] = []
    anchor = row.get("formal_anchor")
    if not isinstance(anchor, dict):
        return ["missing_formal_anchor"]
    if anchor.get("contract") != expected_contract:
        errors.append("anchor_contract_mismatch")
    if anchor.get("qps_used_for_resource_prediction") is not False:
        errors.append("qps_used_for_resource_prediction_not_false")
    if row.get("technical_failure") is not False:
        errors.append("technical_failure")
    if row.get("used_real_replay") is not True or int(row.get("valid_replay_count") or 0) != 1:
        errors.append("not_one_valid_real_replay")
    if row.get("status") != "ok":
        errors.append("replay_status_not_ok")
    if row.get("workload") != artifact.get("benchmark"):
        errors.append("benchmark_mismatch")

    completeness = row.get("workload_completeness")
    if not isinstance(completeness, dict) or completeness.get("passed") is not True:
        errors.append("workload_completeness_failed")
    else:
        scope = artifact.get("scope") or {}
        expected_queries = ((scope.get("plan_evidence") or {}).get("query_sequence"))
        observed_queries = completeness.get("expected_query_sequence")
        if expected_queries is not None and observed_queries != expected_queries:
            errors.append("query_sequence_scope_mismatch")
        expected_clients = ((scope.get("benchmark_concurrency") or {}).get("client_count"))
        # JOB and native XP certify concurrency through their workload-
        # specific completeness payloads rather than ``expected_worker_count``.
        # Compare this generic field only when the validator serialized it;
        # absence is not evidence of a mismatch.
        observed_workers = completeness.get("expected_worker_count")
        if (
            expected_clients is not None
            and observed_workers is not None
            and observed_workers != expected_clients
        ):
            errors.append("client_count_scope_mismatch")

    expected_sf = (artifact.get("scope") or {}).get("scale_factor")
    row_sf = (row.get("hybench_run_config") or {}).get("sf")
    if expected_sf is not None and row_sf is not None and str(row_sf) != str(expected_sf):
        errors.append("scale_factor_scope_mismatch")
    coverage = anchor.get("plan_fingerprint_coverage")
    if not isinstance(coverage, dict) or coverage.get("complete") is not True:
        errors.append("incomplete_plan_fingerprint_coverage")

    parameter_deltas = anchor.get("parameter_log_deltas")
    raw_plan_ratios = anchor.get("raw_plan_log_ratios")
    outcomes = anchor.get("outcomes")
    if not isinstance(parameter_deltas, dict):
        errors.append("missing_parameter_log_deltas")
    if not isinstance(raw_plan_ratios, dict):
        errors.append("missing_raw_plan_log_ratios")
    if not isinstance(outcomes, dict):
        errors.append("missing_outcomes")
    for resource in RESOURCE_NAMES:
        try:
            if isinstance(raw_plan_ratios, dict):
                _finite_number(raw_plan_ratios.get(resource), f"anchor.raw_plan_log_ratios.{resource}")
            if isinstance(outcomes, dict):
                _finite_number(outcomes.get(resource), f"anchor.outcomes.{resource}")
        except ValidationError:
            errors.append(f"invalid_{resource}_anchor_value")
    return errors


def _load_manifest_if_present(anchor_path: Path, row_count: int, expected_contract: str) -> Dict[str, Any]:
    manifest_path = anchor_path.with_suffix(".manifest.json")
    if not manifest_path.exists():
        return {"present": False}
    manifest, manifest_file_sha = _load_json(manifest_path)
    if manifest.get("anchor_contract") != expected_contract:
        raise ValidationError("anchor manifest contract differs from expected anchor contract")
    if int(manifest.get("anchor_count", -1)) != row_count:
        raise ValidationError("anchor manifest count differs from JSONL row count")
    if manifest.get("qps_used_for_resource_prediction") is not False:
        raise ValidationError("anchor manifest must declare qps_used_for_resource_prediction=false")
    if manifest.get("source_campaign_mutated") is not False:
        raise ValidationError("anchor manifest reports source campaign mutation")
    return {
        "present": True,
        "path": str(manifest_path),
        "file_sha256": manifest_file_sha,
        "contract": manifest.get("contract"),
        "anchor_count": row_count,
        "accepted_complete_rounds": manifest.get("accepted_complete_rounds", []),
    }


def _select_latest_complete_group(
    rows: Sequence[Mapping[str, Any]],
    artifact: Mapping[str, Any],
    minimum_points: int,
    expected_contract: str,
) -> Tuple[str, List[Mapping[str, Any]], List[Dict[str, Any]]]:
    grouped: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        anchor = row.get("formal_anchor")
        group = anchor.get("holdout_group") if isinstance(anchor, dict) else None
        if isinstance(group, str) and group:
            grouped[group].append(row)
    if not grouped:
        raise ValidationError("no formal_anchor.holdout_group values found")

    audits: List[Dict[str, Any]] = []
    complete: List[Tuple[str, List[Mapping[str, Any]]]] = []
    for group, group_rows in grouped.items():
        errors: List[str] = []
        if len(group_rows) < minimum_points:
            errors.append(f"group_has_fewer_than_{minimum_points}_points")
        labels = [str(row.get("label") or "") for row in group_rows]
        if not all(labels) or len(labels) != len(set(labels)):
            errors.append("labels_missing_or_duplicate")
        ordinals = [_candidate_ordinal(label) for label in labels]
        if all(value is not None for value in ordinals):
            expected = list(range(1, len(group_rows) + 1))
            if sorted(int(value) for value in ordinals if value is not None) != expected:
                errors.append("candidate_ordinals_not_contiguous_complete_group")
        else:
            errors.append("candidate_ordinal_missing")
        fingerprints = [
            str((row.get("formal_anchor") or {}).get("workload_plan_fingerprint") or "")
            for row in group_rows
        ]
        if not all(fingerprints) or len(fingerprints) != len(set(fingerprints)):
            errors.append("full_plan_fingerprints_missing_or_duplicate")
        for row in group_rows:
            for error in _anchor_row_errors(row, expected_contract, artifact):
                errors.append(f"line_{row.get('_source_line')}:{error}")
        audit = {
            "holdout_group": group,
            "point_count": len(group_rows),
            "complete": not errors,
            "errors": sorted(set(errors)),
            "order": _anchor_group_order(group, group_rows)[0],
        }
        audits.append(audit)
        if not errors:
            complete.append((group, group_rows))
    if not complete:
        raise ValidationError(f"no complete holdout group with at least {minimum_points} points")
    complete.sort(key=lambda item: _anchor_group_order(item[0], item[1]))
    group, selected = complete[-1]
    selected = sorted(selected, key=lambda row: _candidate_ordinal(str(row.get("label"))) or 0)
    audits.sort(key=lambda value: (int(value["order"]), str(value["holdout_group"])))
    return group, selected, audits


def _rankdata(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            ranks[order[position]] = average_rank
        cursor = end
    return ranks


def _spearman(actual: Sequence[float], predicted: Sequence[float]) -> float:
    if len(actual) < 2:
        return 0.0
    actual_rank = _rankdata(actual)
    predicted_rank = _rankdata(predicted)
    actual_mean = sum(actual_rank) / len(actual_rank)
    predicted_mean = sum(predicted_rank) / len(predicted_rank)
    numerator = sum(
        (left - actual_mean) * (right - predicted_mean)
        for left, right in zip(actual_rank, predicted_rank)
    )
    left_norm = math.sqrt(sum((value - actual_mean) ** 2 for value in actual_rank))
    right_norm = math.sqrt(sum((value - predicted_mean) ** 2 for value in predicted_rank))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _direction(value: float, deadband: float) -> int:
    if value < -deadband:
        return -1
    if value > deadband:
        return 1
    return 0


def _metrics(
    rows: Sequence[Mapping[str, Any]],
    artifact: Mapping[str, Any],
    resource: str,
    deadband: float,
) -> Dict[str, Any]:
    active_params, coefficients, plan_coefficient = _artifact_model(artifact, resource)
    actual: List[float] = []
    predicted: List[float] = []
    labels: List[str] = []
    for row in rows:
        anchor = row["formal_anchor"]
        parameter_deltas = anchor["parameter_log_deltas"]
        unknown = set(parameter_deltas) - set(active_params)
        if unknown:
            raise ValidationError(f"anchor has parameter deltas outside artifact active_params: {sorted(unknown)}")
        parameter_prediction = 0.0
        for parameter in active_params:
            value = _finite_number(parameter_deltas.get(parameter, 0.0), f"anchor.{parameter}")
            parameter_prediction += coefficients[parameter] * value
        raw_plan_ratio = _finite_number(anchor["raw_plan_log_ratios"][resource], f"anchor.raw_plan.{resource}")
        predicted.append(parameter_prediction + plan_coefficient * raw_plan_ratio)
        actual.append(_finite_number(anchor["outcomes"][resource], f"anchor.outcomes.{resource}"))
        labels.append(str(row.get("label")))

    absolute_errors = [abs(left - right) for left, right in zip(actual, predicted)]
    actual_directions = [_direction(value, deadband) for value in actual]
    predicted_directions = [_direction(value, deadband) for value in predicted]
    direction_error_indices = [
        index for index, pair in enumerate(zip(actual_directions, predicted_directions)) if pair[0] != pair[1]
    ]
    actual_best_index = min(range(len(actual)), key=lambda index: (actual[index], labels[index]))
    predicted_rank = 1 + sum(value < predicted[actual_best_index] for value in predicted)
    return {
        "formula": "sum(parameter_coefficients[p] * parameter_log_deltas[p]) + "
        "plan_log_ratio_coefficient * raw_plan_log_ratios[resource]",
        "qps_used": False,
        "point_count": len(rows),
        "mae_log_ratio": sum(absolute_errors) / len(absolute_errors),
        "spearman": _spearman(actual, predicted),
        "direction_deadband_log_ratio": deadband,
        "direction_error_count": len(direction_error_indices),
        "direction_error_labels": [labels[index] for index in direction_error_indices],
        "actual_best_label": labels[actual_best_index],
        "actual_best_predicted_rank": predicted_rank,
        "actual": actual,
        "predicted": predicted,
        "labels": labels,
    }


def _promotion_recommendation(
    old_metrics: Mapping[str, Mapping[str, Any]],
    new_metrics: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    tolerance = 1e-12
    per_resource: Dict[str, Any] = {}
    all_non_regressing = True
    any_strict_improvement = False
    for resource in RESOURCE_NAMES:
        old = old_metrics[resource]
        new = new_metrics[resource]
        checks = {
            "mae_not_worse": float(new["mae_log_ratio"]) <= float(old["mae_log_ratio"]) + tolerance,
            "spearman_not_worse": float(new["spearman"]) + tolerance >= float(old["spearman"]),
            "direction_errors_not_worse": int(new["direction_error_count"]) <= int(old["direction_error_count"]),
            "actual_best_rank_not_worse": int(new["actual_best_predicted_rank"])
            <= int(old["actual_best_predicted_rank"]),
        }
        strict = (
            float(new["mae_log_ratio"]) < float(old["mae_log_ratio"]) - tolerance
            or float(new["spearman"]) > float(old["spearman"]) + tolerance
            or int(new["direction_error_count"]) < int(old["direction_error_count"])
            or int(new["actual_best_predicted_rank"]) < int(old["actual_best_predicted_rank"])
        )
        non_regressing = all(checks.values())
        per_resource[resource] = {
            "checks": checks,
            "non_regressing": non_regressing,
            "strict_improvement": strict,
        }
        all_non_regressing = all_non_regressing and non_regressing
        any_strict_improvement = any_strict_improvement or strict
    promote = all_non_regressing and any_strict_improvement
    return {
        "recommend_promotion": promote,
        "policy": "all_resources_non_regressing_on_mae_spearman_direction_errors_actual_best_rank_and_at_least_one_strict_improvement_v1",
        "artifact_mutated": False,
        "experiment_mutated": False,
        "per_resource": per_resource,
        "reason": "new_artifact_passes_holdout_comparison_policy" if promote else "new_artifact_regresses_or_does_not_improve",
    }


def compare_artifacts(
    anchor_path: Path,
    old_artifact_path: Path,
    new_artifact_path: Path,
    *,
    minimum_points: int = 5,
    anchor_contract: str = ANCHOR_CONTRACT,
    direction_deadband_fraction: float = 0.01,
) -> Dict[str, Any]:
    if minimum_points < 5:
        raise ValidationError("minimum_points cannot be lower than 5")
    if direction_deadband_fraction < 0:
        raise ValidationError("direction_deadband_fraction cannot be negative")
    rows = _load_jsonl(anchor_path)
    old, old_sha = _load_json(old_artifact_path)
    new, new_sha = _load_json(new_artifact_path)
    pair_validation = _validate_artifact_pair(old, new)
    manifest = _load_manifest_if_present(anchor_path, len(rows), anchor_contract)
    holdout_group, holdout_rows, group_audits = _select_latest_complete_group(
        rows,
        old,
        minimum_points,
        anchor_contract,
    )
    deadband = math.log1p(direction_deadband_fraction)
    old_metrics = {resource: _metrics(holdout_rows, old, resource, deadband) for resource in RESOURCE_NAMES}
    new_metrics = {resource: _metrics(holdout_rows, new, resource, deadband) for resource in RESOURCE_NAMES}
    comparisons: Dict[str, Any] = {}
    for resource in RESOURCE_NAMES:
        comparisons[resource] = {
            "old": old_metrics[resource],
            "new": new_metrics[resource],
            "delta_new_minus_old": {
                "mae_log_ratio": new_metrics[resource]["mae_log_ratio"] - old_metrics[resource]["mae_log_ratio"],
                "spearman": new_metrics[resource]["spearman"] - old_metrics[resource]["spearman"],
                "direction_error_count": new_metrics[resource]["direction_error_count"]
                - old_metrics[resource]["direction_error_count"],
                "actual_best_predicted_rank": new_metrics[resource]["actual_best_predicted_rank"]
                - old_metrics[resource]["actual_best_predicted_rank"],
            },
        }
    return {
        "contract": REPORT_CONTRACT,
        "read_only": True,
        "inputs": {
            "anchors": str(anchor_path),
            "old_artifact": str(old_artifact_path),
            "old_artifact_file_sha256": old_sha,
            "new_artifact": str(new_artifact_path),
            "new_artifact_file_sha256": new_sha,
        },
        "validation": {
            **pair_validation,
            "anchor_contract": anchor_contract,
            "anchor_contract_valid": True,
            "minimum_complete_group_points": minimum_points,
            "manifest": manifest,
            "qps_field_present_but_ignored": any(
                "qps_outer_only" in ((row.get("formal_anchor") or {}).get("outcomes") or {}) for row in holdout_rows
            ),
            "qps_used_for_resource_prediction": False,
        },
        "holdout": {
            "selection": "latest_complete_holdout_group",
            "holdout_group": holdout_group,
            "point_count": len(holdout_rows),
            "labels": [str(row.get("label")) for row in holdout_rows],
            "group_audits": group_audits,
        },
        "resources": comparisons,
        "promotion": _promotion_recommendation(old_metrics, new_metrics),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors", required=True, type=Path, help="formal replay anchor JSONL")
    parser.add_argument("--old-artifact", required=True, type=Path)
    parser.add_argument("--new-artifact", required=True, type=Path)
    parser.add_argument("--minimum-points", type=int, default=5)
    parser.add_argument("--anchor-contract", default=ANCHOR_CONTRACT)
    parser.add_argument("--direction-deadband-fraction", type=float, default=0.01)
    parser.add_argument("--output", type=Path, help="optional report path; artifacts are never modified")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = compare_artifacts(
            args.anchors,
            args.old_artifact,
            args.new_artifact,
            minimum_points=args.minimum_points,
            anchor_contract=args.anchor_contract,
            direction_deadband_fraction=args.direction_deadband_fraction,
        )
    except (OSError, ValidationError) as exc:
        print(json.dumps({"contract": REPORT_CONTRACT, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
