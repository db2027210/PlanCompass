#!/usr/bin/env python3
"""Run or analyze one isolated MySQL Algorithm C gradient-debug round.

The run mode keeps the formal 2^k-1 subset and fixed Top-5 replay contracts,
but caps the campaign at one outer attempt.  It also forces the unmodified PG
gradient source and writes checkpoints below a dedicated debug directory.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


REQUIRED_GRADIENT_FIELDS = (
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_json(path: Path) -> Dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return document


def _read_gradient_events(log_path: Path) -> list[Dict[str, Any]]:
    events: list[Dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL {log_path}:{line_number}: {exc}") from exc
            if isinstance(row, dict) and row.get("event") == "score_gradient_debug":
                row["_log_path"] = str(log_path)
                row["_line_number"] = line_number
                events.append(row)
    return events


def _resolve_log_path(raw: Any, result_path: Path, log_root: Optional[Path]) -> Path:
    path = Path(str(raw))
    if path.is_file():
        return path
    if log_root is not None:
        candidate = log_root / path.name
        if candidate.is_file():
            return candidate
        matches = list(log_root.rglob(path.name))
        if len(matches) == 1:
            return matches[0]
    candidate = result_path.parent / path
    return candidate


def _first_order_gain(event: Mapping[str, Any]) -> float:
    gradients = event.get("g_j") or {}
    deltas = event.get("delta_param") or {}
    if not isinstance(gradients, Mapping) or not isinstance(deltas, Mapping):
        return 0.0
    total = 0.0
    for name, gradient in gradients.items():
        component = deltas.get(name)
        if not isinstance(component, Mapping):
            continue
        relative = _finite(component.get("relative"))
        g_value = _finite(gradient)
        if relative is not None and g_value is not None:
            total += g_value * relative
    return total


def _event_diagnostics(event: Mapping[str, Any]) -> list[str]:
    diagnostics: list[str] = []
    gradients = event.get("g_j") or {}
    if isinstance(gradients, Mapping) and gradients:
        finite_gradients = [_finite(value) for value in gradients.values()]
        if all(value is not None and abs(value) <= 1e-15 for value in finite_gradients):
            diagnostics.append("all_gradients_zero")
    ratios = event.get("feature_ratio") or {}
    if isinstance(ratios, Mapping) and ratios:
        finite_ratios = [_finite(value) for value in ratios.values()]
        if all(value is not None and abs(value - 1.0) <= 1e-12 for value in finite_ratios):
            diagnostics.append("plan_physics_feature_plateau")
    deltas = event.get("delta_param") or {}
    if isinstance(deltas, Mapping) and deltas:
        if all(bool((value or {}).get("clipped")) for value in deltas.values() if isinstance(value, Mapping)):
            diagnostics.append("all_parameter_updates_clipped")
        if all(
            abs(_finite((value or {}).get("absolute")) or 0.0) <= 1e-15
            for value in deltas.values()
            if isinstance(value, Mapping)
        ):
            diagnostics.append("all_parameter_updates_zero")
    score_delta = _finite(event.get("candidate_score_delta"))
    score_delta_vs_initial = _finite(event.get("candidate_score_delta_vs_initial"))
    if _first_order_gain(event) > 1e-15 and score_delta is not None and score_delta <= 0.0:
        diagnostics.append("positive_local_gradient_but_nonimproving_candidate")
    if (
        score_delta is not None
        and score_delta < 0.0
        and score_delta_vs_initial is not None
        and score_delta_vs_initial > 0.0
    ):
        diagnostics.append("candidate_regresses_incumbent_but_beats_r0")
    relative_steps = [
        abs(_finite(value.get("relative")) or 0.0)
        for value in deltas.values()
        if isinstance(value, Mapping)
    ] if isinstance(deltas, Mapping) else []
    if relative_steps and max(relative_steps) < 1e-4:
        diagnostics.append("parameter_step_too_small_to_cross_plan_boundary")
    return diagnostics


def _percentile(values: Sequence[float], percentile: float) -> Optional[float]:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    position = (len(ordered) - 1) * percentile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _trajectory_summary(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    plan_hashes = [
        str(event.get("candidate_plan_work_sha256"))
        for event in events
        if event.get("candidate_plan_work_sha256")
    ]
    scores = [
        value
        for value in (_finite(event.get("candidate_score")) for event in events)
        if value is not None
    ]
    etas = [
        value
        for value in (_finite(event.get("eta")) for event in events)
        if value is not None
    ]
    relative_steps = [
        abs(value)
        for event in events
        for delta in ((event.get("delta_param") or {}).values())
        if isinstance(delta, Mapping)
        for value in [_finite(delta.get("relative"))]
        if value is not None
    ]
    ratio_values = [
        value
        for event in events
        for value in (
            _finite(raw)
            for raw in ((event.get("feature_ratio") or {}).values())
        )
        if value is not None
    ]
    score_span = max(scores) - min(scores) if scores else None
    eta_decay_count = sum(
        1 for before, after in zip(etas, etas[1:]) if after < before
    )
    all_feature_ratios_unity = bool(ratio_values) and all(
        abs(value - 1.0) <= 1e-12 for value in ratio_values
    )
    piecewise_constant_plan_trap = bool(
        events
        and plan_hashes
        and len(set(plan_hashes)) == 1
        and score_span is not None
        and abs(score_span) <= 1e-12
        and all_feature_ratios_unity
        and eta_decay_count > 0
    )
    return {
        "distinct_candidate_plan_work_count": len(set(plan_hashes)),
        "candidate_plan_work_sequence": plan_hashes,
        "candidate_score_sequence": scores,
        "candidate_score_span": score_span,
        "candidate_score_regression_count": sum(
            1
            for event in events
            if (_finite(event.get("candidate_score_delta")) or 0.0) < 0.0
        ),
        "eta_sequence": etas,
        "eta_decay_count": eta_decay_count,
        "all_feature_ratios_unity": all_feature_ratios_unity,
        "piecewise_constant_plan_trap": piecewise_constant_plan_trap,
        "piecewise_constant_plan_trap_evidence": {
            "single_plan_work": bool(plan_hashes) and len(set(plan_hashes)) == 1,
            "constant_candidate_score": score_span is not None and abs(score_span) <= 1e-12,
            "all_feature_ratios_unity": all_feature_ratios_unity,
            "eta_decayed_after_rejection": eta_decay_count > 0,
        },
        "maximum_absolute_relative_parameter_step": max(relative_steps, default=0.0),
    }


def _prediction_calibration(
    replay_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    per_candidate: list[Dict[str, Any]] = []
    errors_by_resource: Dict[str, list[float]] = {
        "cpu": [], "memory": [], "io": [],
    }
    direction_by_resource: Dict[str, list[bool]] = {
        "cpu": [], "memory": [], "io": [],
    }
    coverage_by_resource: Dict[str, list[bool]] = {
        "cpu": [], "memory": [], "io": [],
    }
    for row in replay_rows:
        predicted = row.get("predicted_utilization") or {}
        measured = row.get("measured_utilization") or {}
        predicted_baseline = row.get("predicted_baseline_utilization") or {}
        measured_baseline = row.get("measured_baseline_utilization") or {}
        intervals = row.get("prediction_intervals") or {}
        resource_errors: Dict[str, Any] = {}
        for resource in ("cpu", "memory", "io"):
            predicted_value = _finite(predicted.get(resource)) if isinstance(predicted, Mapping) else None
            measured_value = _finite(measured.get(resource)) if isinstance(measured, Mapping) else None
            if predicted_value is None or measured_value is None:
                resource_errors[resource] = {"available": False}
                continue
            absolute_error = predicted_value - measured_value
            relative_error = (
                abs(absolute_error) / abs(measured_value)
                if abs(measured_value) > 1e-12
                else None
            )
            if relative_error is not None:
                errors_by_resource[resource].append(relative_error)
            predicted_base = (
                _finite(predicted_baseline.get(resource))
                if isinstance(predicted_baseline, Mapping) else None
            )
            measured_base = (
                _finite(measured_baseline.get(resource))
                if isinstance(measured_baseline, Mapping) else None
            )
            direction_correct = None
            if predicted_base is not None and measured_base is not None:
                predicted_delta = predicted_value - predicted_base
                measured_delta = measured_value - measured_base
                direction_correct = (
                    abs(predicted_delta) <= 1e-12
                    and abs(measured_delta) <= 1e-12
                ) or predicted_delta * measured_delta > 0.0
                direction_by_resource[resource].append(direction_correct)
            interval = intervals.get(resource) if isinstance(intervals, Mapping) else None
            covered = None
            if isinstance(interval, Mapping):
                lower = _finite(interval.get("lower"))
                upper = _finite(interval.get("upper"))
                if lower is not None and upper is not None and lower <= upper:
                    covered = lower <= measured_value <= upper
                    coverage_by_resource[resource].append(covered)
            resource_errors[resource] = {
                "available": True,
                "predicted": predicted_value,
                "measured": measured_value,
                "signed_error": absolute_error,
                "absolute_relative_error": relative_error,
                "direction_correct": direction_correct,
                "prediction_interval": (
                    copy.deepcopy(dict(interval)) if isinstance(interval, Mapping) else None
                ),
                "prediction_interval_covered": covered,
            }
        per_candidate.append(
            {
                "rank": row.get("rank"),
                "subset_id": row.get("subset_id"),
                "resources": resource_errors,
            }
        )
    summary: Dict[str, Any] = {}
    calibration_required = False
    for resource, errors in errors_by_resource.items():
        median = _percentile(errors, 0.5)
        p90 = _percentile(errors, 0.9)
        direction_samples = direction_by_resource[resource]
        coverage_samples = coverage_by_resource[resource]
        direction_accuracy = (
            sum(direction_samples) / len(direction_samples)
            if direction_samples else None
        )
        interval_coverage = (
            sum(coverage_samples) / len(coverage_samples)
            if coverage_samples else None
        )
        passed = (
            len(errors) >= 5
            and median is not None
            and p90 is not None
            and median <= 0.15
            and p90 <= 0.30
            and direction_accuracy is not None
            and direction_accuracy >= 0.80
            and interval_coverage is not None
            and 0.80 <= interval_coverage <= 0.90
        )
        if errors and not passed:
            calibration_required = True
        summary[resource] = {
            "sample_count": len(errors),
            "median_absolute_relative_error": median,
            "p90_absolute_relative_error": p90,
            "direction_sample_count": len(direction_samples),
            "direction_accuracy": direction_accuracy,
            "interval_sample_count": len(coverage_samples),
            "prediction_interval_coverage": interval_coverage,
            "thresholds": {
                "minimum_samples": 5,
                "median_max": 0.15,
                "p90_max": 0.30,
                "direction_accuracy_min": 0.80,
                "prediction_interval_coverage_min": 0.80,
                "prediction_interval_coverage_max": 0.90,
            },
            "passed": passed if errors else None,
        }
    return {
        "per_candidate": per_candidate,
        "per_resource": summary,
        "calibration_required": calibration_required,
        "qps_used": False,
    }


def analyze_result(result_path: Path, *, log_root: Optional[Path] = None) -> Dict[str, Any]:
    result_path = result_path.resolve()
    document = _read_json(result_path)
    analysis_document = document
    analyzed_result_path = result_path
    if not document.get("all_candidate_rankings"):
        outer_rows = document.get("outer_loop") or []
        last_outer = outer_rows[-1] if isinstance(outer_rows, list) and outer_rows else {}
        campaign_round = last_outer.get("campaign_round") if isinstance(last_outer, Mapping) else {}
        round_result_raw = (
            campaign_round.get("round_result_path")
            if isinstance(campaign_round, Mapping)
            else None
        )
        if round_result_raw:
            round_result_path = Path(str(round_result_raw))
            if not round_result_path.is_absolute():
                round_result_path = result_path.parent / round_result_path
            if round_result_path.is_file():
                analyzed_result_path = round_result_path.resolve()
                analysis_document = _read_json(analyzed_result_path)
    rankings = analysis_document.get("all_candidate_rankings") or []
    if not isinstance(rankings, list):
        raise ValueError("result all_candidate_rankings must be a list")
    subset_rows: list[Dict[str, Any]] = []
    missing_logs: list[str] = []
    all_events: list[Dict[str, Any]] = []
    for ranking in rankings:
        if not isinstance(ranking, Mapping):
            continue
        log_path = _resolve_log_path(ranking.get("log_path"), result_path, log_root)
        if not log_path.is_file():
            missing_logs.append(str(log_path))
            events: list[Dict[str, Any]] = []
        else:
            events = _read_gradient_events(log_path)
        all_events.extend(events)
        subset_rows.append(
            {
                "subset_id": ranking.get("subset_id"),
                "active_subset": copy.deepcopy(ranking.get("active_subset") or []),
                "final_score": ranking.get("final_score"),
                "score_improvement": ranking.get("score_improvement"),
                "gradient_event_count": len(events),
                "log_path": str(log_path),
                "diagnostics": sorted(
                    {diagnostic for event in events for diagnostic in _event_diagnostics(event)}
                ),
                "trajectory": _trajectory_summary(events),
            }
        )
    missing_fields = [
        {
            "log_path": event.get("_log_path"),
            "line_number": event.get("_line_number"),
            "iteration": event.get("iteration"),
            "fields": [name for name in REQUIRED_GRADIENT_FIELDS if name not in event],
        }
        for event in all_events
        if any(name not in event for name in REQUIRED_GRADIENT_FIELDS)
    ]
    invalid_fields = []
    mapping_fields = (
        "feature_ratio", "predicted_resource", "utilization", "dScore/dU",
        "T", "confidence", "g_j", "delta_param",
    )
    for event in all_events:
        invalid = [
            name for name in mapping_fields
            if not isinstance(event.get(name), Mapping) or not event.get(name)
        ]
        invalid.extend(
            name for name in ("eta", "candidate_score")
            if _finite(event.get(name)) is None
        )
        if invalid:
            invalid_fields.append({
                "log_path": event.get("_log_path"),
                "line_number": event.get("_line_number"),
                "iteration": event.get("iteration"),
                "fields": sorted(set(invalid)),
            })
    diagnostic_counts: Dict[str, int] = {}
    for event in all_events:
        for diagnostic in _event_diagnostics(event):
            diagnostic_counts[diagnostic] = diagnostic_counts.get(diagnostic, 0) + 1
    trapped_subsets = [
        str(row.get("subset_id"))
        for row in subset_rows
        if (row.get("trajectory") or {}).get("piecewise_constant_plan_trap") is True
    ]
    improving = sum(
        1
        for event in all_events
        if (_finite(event.get("candidate_score_delta")) or 0.0) > 0.0
    )
    outer_loop = analysis_document.get("outer_loop") or []
    outer = outer_loop[-1] if isinstance(outer_loop, list) and outer_loop else {}
    replay = outer.get("real_replay") if isinstance(outer, Mapping) else {}
    evaluations = replay.get("evaluations") if isinstance(replay, Mapping) else []
    queue = analysis_document.get("replay_candidate_queue") or []
    top5 = [
        {
            "rank": row.get("rank"),
            "subset_id": row.get("subset_id"),
            "predicted_score": row.get("final_score"),
            "predicted_resource": copy.deepcopy(row.get("final_resource") or {}),
        }
        for row in queue[:5]
        if isinstance(row, Mapping)
    ]
    expected_top5_count = min(5, len(queue))
    top5_ranks = [row.get("rank") for row in top5]
    top5_scores = [_finite(row.get("predicted_score")) for row in top5]
    top5_contract_violations = []
    if len(top5) != expected_top5_count:
        top5_contract_violations.append("top5_cardinality_mismatch")
    if top5_ranks != list(range(1, expected_top5_count + 1)):
        top5_contract_violations.append("top5_ranks_not_unique_contiguous_1_to_k")
    if any(value is None for value in top5_scores) or any(
        left < right
        for left, right in zip(top5_scores, top5_scores[1:])
        if left is not None and right is not None
    ):
        top5_contract_violations.append("top5_not_sorted_by_candidate_score_desc")
    replay_rows = [
        {
            "rank": row.get("candidate_rank"),
            "subset_id": row.get("candidate_subset_id"),
            "predicted_utilization": copy.deepcopy(
                row.get("candidate_predicted_final_utilization") or {}
            ),
            "predicted_baseline_utilization": copy.deepcopy(
                row.get("candidate_predicted_initial_utilization") or {}
            ),
            "measured_utilization": copy.deepcopy(
                ((row.get("candidate_final_validation") or {}).get("utilization_pct") or {})
            ),
            "measured_baseline_utilization": copy.deepcopy(
                row.get("baseline_measured_utilization") or {}
            ),
            "prediction_intervals": copy.deepcopy(
                row.get("candidate_predicted_utilization_intervals") or {}
            ),
            "outer_eligible": (row.get("candidate_final_validation") or {}).get("outer_eligible"),
            "outer_gate_reasons": copy.deepcopy(
                (((row.get("candidate_final_validation") or {}).get("outer_gate") or {}).get("reasons") or [])
            ),
            "three_level_fingerprint_usable": (
                (row.get("three_level_fingerprint") or {}).get(
                    "true_duplicate_usable"
                )
            ),
            "three_level_fingerprint_ineligible_reasons": copy.deepcopy(
                (row.get("three_level_fingerprint") or {}).get(
                    "true_duplicate_ineligible_reasons"
                )
                or []
            ),
        }
        for row in (evaluations or [])
        if isinstance(row, Mapping)
    ]
    report = {
        "schema_version": "mysql_original_gradient_debug_report_v1",
        "result_path": str(result_path),
        "analyzed_round_result_path": str(analyzed_result_path),
        "gradient_contract": {
            "source": "original_dScore_dU_times_T_times_confidence",
            "required_fields": list(REQUIRED_GRADIENT_FIELDS),
            "qps_used_by_gradient": False,
        },
        "subset_count": len(rankings),
        "gradient_event_count": len(all_events),
        "gradient_events_with_improving_score": improving,
        "gradient_event_improvement_rate": (
            improving / len(all_events) if all_events else None
        ),
        "missing_log_paths": missing_logs,
        "missing_required_gradient_fields": missing_fields,
        "invalid_required_gradient_fields": invalid_fields,
        "diagnostic_counts": diagnostic_counts,
        "piecewise_constant_plan_trap": {
            "detected": bool(trapped_subsets),
            "affected_subset_count": len(trapped_subsets),
            "affected_subset_ids": trapped_subsets,
            "interpretation": (
                "analytic gradient steps did not cross a MySQL plan-physics boundary; "
                "rejected equal-score steps were followed by eta decay"
                if trapped_subsets
                else None
            ),
        },
        "subset_diagnostics": subset_rows,
        "predicted_top5": top5,
        "top5_contract": {
            "expected_count": expected_top5_count,
            "valid": not top5_contract_violations,
            "violations": top5_contract_violations,
        },
        "real_replay_top5": replay_rows,
        "real_replay_count": len(replay_rows),
        "prediction_calibration": _prediction_calibration(replay_rows),
    }
    return report


def prepare_debug_config(config: Mapping[str, Any], output_root: Optional[Path]) -> Dict[str, Any]:
    debug = copy.deepcopy(dict(config))
    debug["measurement_seconds"] = 600
    debug["minimum_measurement_seconds"] = 180
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    configured_output = Path(str((debug.get("output") or {}).get("directory", "history/mysql")))
    root = (output_root or configured_output / "gradient_debug_rounds").resolve()
    output = root / stamp
    debug.setdefault("output", {})["directory"] = str(output)
    optimizer = debug.setdefault("optimizer", {})
    optimizer["original_gradient_only"] = True
    optimizer["round_index"] = 1
    # MySQL plan physics is piecewise constant in cost-parameter space.  Keep
    # the original analytic gradient, but enlarge eta along that same gradient
    # while an equal-score plan cell is observed so the debug round can reach a
    # genuine plan boundary.  The plateau point is only a replay probe and does
    # not update the inner or outer incumbent.
    optimizer["boundary_aware_eta_enabled"] = True
    optimizer["boundary_eta_growth_factor"] = 2.0
    optimizer["plateau_exploration_enabled"] = False
    optimizer["min_param_delta_rel_pct"] = 10.0
    optimizer["max_param_delta_rel_pct"] = 32.0
    optimizer["plateau_score_epsilon"] = 1e-12
    outer = debug.setdefault("formal_outer_protocol", {})
    outer["max_outer_attempts"] = 1
    outer["accepted_shrink_count"] = 0
    outer["rejected_count"] = 0
    outer["formal_checkpoint_path"] = str(output / "formal_outer_checkpoint.json")
    outer["formal_real_r0_checkpoint_path"] = str(output / "formal_real_r0.json")
    outer["formal_point_journal_root"] = str(output / "point_journals")
    debug["exp_id"] = f"{debug.get('exp_id', 'mysql_algorithm_c')}.gradient_debug.{stamp}"
    debug["debug_contract"] = {
        "schema_version": "mysql_original_gradient_one_outer_round_v2",
        "outer_attempt_cap": 1,
        "all_nonempty_subsets_required": True,
        "fixed_real_replay_top_k": 5,
        "real_replay_timing": {
            "warmup_seconds": 60,
            "minimum_measurement_seconds": 180,
            "decision_step_seconds": 30,
            "maximum_measurement_seconds": 600,
            "stop_policy": "latest_six_30_second_resource_windows_stable",
        },
        "original_gradient_only": True,
        "qps_role": "outer_acceptance_only",
    }
    return debug


def load_resume_debug_config(directory: Path) -> Dict[str, Any]:
    """Load one previously sealed debug run without creating a new campaign."""
    raw_directory = Path(directory)
    output = raw_directory.resolve()
    if raw_directory.is_symlink() or not output.is_dir():
        raise ValueError(f"resume directory is absent or a symlink: {output}")
    config_path = output / "debug_config.json"
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError(f"resume debug_config.json is absent or a symlink: {config_path}")
    debug = _read_json(config_path)
    contract = debug.get("debug_contract") or {}
    if contract.get("schema_version") not in {
        "mysql_original_gradient_one_outer_round_v1",
        "mysql_original_gradient_one_outer_round_v2",
    }:
        raise ValueError("resume directory is not an original-gradient debug campaign")
    configured_output = Path(str((debug.get("output") or {}).get("directory", ""))).resolve()
    if configured_output != output:
        raise ValueError(
            f"resume output binding mismatch: config={configured_output}, requested={output}"
        )
    protocol = debug.get("formal_outer_protocol") or {}
    expected = {
        "formal_checkpoint_path": output / "formal_outer_checkpoint.json",
        "formal_real_r0_checkpoint_path": output / "formal_real_r0.json",
    }
    for key, expected_path in expected.items():
        configured = Path(str(protocol.get(key, ""))).resolve()
        if configured != expected_path or not expected_path.is_file():
            raise ValueError(
                f"resume checkpoint binding is invalid for {key}: "
                f"config={configured}, expected={expected_path}"
            )
    return debug


def _write_report(report: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--readiness-manifest", type=Path)
    parser.add_argument("--r0-authority-readiness-manifest", type=Path)
    parser.add_argument("--service-factory")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--resume-directory", type=Path)
    parser.add_argument("--analyze-result", type=Path)
    parser.add_argument("--log-root", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    if args.analyze_result is not None:
        report = analyze_result(args.analyze_result, log_root=args.log_root)
        report_path = args.report or args.analyze_result.with_name(
            args.analyze_result.stem + ".gradient_debug.json"
        )
        _write_report(report, report_path)
        print(json.dumps({"report_path": str(report_path.resolve()), **report}, ensure_ascii=False))
        return 0 if not report["missing_required_gradient_fields"] else 2

    missing = [
        name
        for name, value in (
            ("--config or --resume-directory", args.config or args.resume_directory),
            ("--readiness-manifest", args.readiness_manifest),
            ("--service-factory", args.service_factory),
        )
        if value is None
    ]
    if missing:
        parser.error("run mode requires " + ", ".join(missing))

    from formal_mysql_sgd_campaign import run_production_campaign
    from mysql_parameter_optimizer import load_config

    if args.resume_directory is not None:
        if args.output_root is not None:
            parser.error("--resume-directory cannot be combined with --output-root")
        debug_config = load_resume_debug_config(args.resume_directory)
    else:
        debug_config = prepare_debug_config(load_config(args.config), args.output_root)
    output = Path(str(debug_config["output"]["directory"]))
    output.mkdir(parents=True, exist_ok=True)
    debug_config_path = output / "debug_config.json"
    if args.resume_directory is None:
        _write_report(debug_config, debug_config_path)
    result = run_production_campaign(
        debug_config,
        readiness_manifest=args.readiness_manifest,
        service_factory_spec=args.service_factory,
        r0_authority_readiness_manifest=args.r0_authority_readiness_manifest,
    )
    result_path = Path(str(result.result_path))
    report = analyze_result(result_path)
    report["debug_config_path"] = str(debug_config_path.resolve())
    report_path = args.report or output / "gradient_debug_report.json"
    _write_report(report, report_path)
    print(
        json.dumps(
            {
                "campaign_status": getattr(result, "campaign_status", None),
                "result_path": str(result_path.resolve()),
                "report_path": str(report_path.resolve()),
                "real_replay_count": report["real_replay_count"],
                "diagnostic_counts": report["diagnostic_counts"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["real_replay_count"] == 5 else 2


if __name__ == "__main__":
    raise SystemExit(main())
