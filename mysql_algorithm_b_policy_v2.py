"""Algorithm B policy extension for a frozen custom calibration design."""

from __future__ import annotations

import copy
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable, Dict, Mapping, Sequence


FRAMEWORK_ROOT = Path("/home/anonymous/program/resource_adaptive")
if str(FRAMEWORK_ROOT) not in sys.path:
    sys.path.append(str(FRAMEWORK_ROOT))

from double_loop_search_common import ParameterRange
from double_loop_scenario import (
    FreshExperienceModel,
    FreshModelQualityThresholds,
    measurement_from_payload,
)
import mysql_algorithm_b_policy as v1


mysql_fresh_model_calibration_points = v1.mysql_fresh_model_calibration_points
preflight_resource_error_bounds = v1.preflight_resource_error_bounds


def _validated_design_override(
    design: Sequence[Mapping[str, Any]],
    base_params: Mapping[str, float],
    ranges: Mapping[str, ParameterRange],
    candidates: Sequence[str],
) -> list[Dict[str, Any]]:
    if len(design) != 15:
        raise RuntimeError("custom calibration design must contain exactly 15 points")
    result: list[Dict[str, Any]] = []
    signatures = set()
    base_names = set(base_params)
    candidate_names = set(candidates)
    for rank, raw in enumerate(design, 1):
        row = copy.deepcopy(dict(raw))
        params = row.get("params")
        changed = row.get("changed_parameters")
        if (
            row.get("calibration_id") != f"calibration_{rank:03d}"
            or not isinstance(params, Mapping)
            or set(params) != base_names
            or not isinstance(changed, list)
            or not changed
            or not set(changed).issubset(candidate_names)
        ):
            raise RuntimeError("custom calibration design identity differs")
        canonical = {name: float(value) for name, value in params.items()}
        if any(not math.isfinite(value) for value in canonical.values()):
            raise RuntimeError("custom calibration design contains non-finite value")
        for name in candidates:
            bound = ranges[name]
            value = canonical[name]
            if value < bound.min_value or value > bound.max_value:
                raise RuntimeError("custom calibration design exceeds frozen range")
        signature = v1._signature(canonical)
        if signature in signatures or signature == v1._signature(base_params):
            raise RuntimeError("custom calibration design point is not distinct")
        signatures.add(signature)
        row["params"] = canonical
        result.append(row)
    return result


def run_mysql_fresh_model_quality_gate(
    model: FreshExperienceModel,
    validator: Callable[[Any, Dict[str, float]], Mapping[str, Any]],
    sql_set: Any,
    base_params: Mapping[str, float],
    ranges: Mapping[str, ParameterRange],
    candidates: Sequence[str],
    *,
    scenario_dir: Path,
    thresholds: FreshModelQualityThresholds | None = None,
    design_override: Sequence[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Run the Top15 gate against the exact frozen design that was replayed."""
    if design_override is None:
        return v1.run_mysql_fresh_model_quality_gate(
            model, validator, sql_set, base_params, ranges, candidates,
            scenario_dir=scenario_dir, thresholds=thresholds,
        )
    thresholds = thresholds or FreshModelQualityThresholds()
    design = _validated_design_override(
        design_override, base_params, ranges, candidates,
    )
    attempts: list[Dict[str, Any]] = []
    validation_rows = []
    log_path = scenario_dir / "fresh_model_calibration.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        for design_row in design:
            replay: Dict[str, Any] = {
                "event": "fresh_model_calibration_replay",
                **copy.deepcopy(design_row),
                "current_run_only": True,
                "calibration_real_result_reused_as_candidate_replay": False,
                "frozen_design_override": True,
            }
            try:
                raw = validator(sql_set, dict(design_row["params"]))
                measurement = measurement_from_payload(
                    raw.get("resources") or raw.get("resource") or {}, raw,
                )
                row = model.build_experience(
                    design_row["params"], measurement.as_resource(), raw,
                )
                validation_rows.append(row)
                model.add(design_row["params"], measurement.as_resource(), raw)
                replay.update({
                    "status": "usable",
                    "measurement": measurement.as_resource(),
                    "real_resource_score": model.objective.resource_score_details(
                        measurement
                    )["total"],
                })
            except Exception as exc:
                replay.update({
                    "status": "failed",
                    "failure_reason": f"{type(exc).__name__}: {exc}",
                })
            attempts.append(copy.deepcopy(replay))
            handle.write(json.dumps(v1._json_value(replay), ensure_ascii=False) + "\n")
            handle.flush()
        baseline_row = model.build_experience(
            base_params, model.objective.baseline.as_resource()
        )
        quality = model.select_quality_mode(
            thresholds,
            validation_rows=validation_rows,
            fixed_training_rows=None,
            always_training_rows=[baseline_row],
        )
        handle.write(json.dumps(v1._json_value({
            "event": "fresh_model_quality_gate_attempt", **quality,
        }), ensure_ascii=False) + "\n")
        handle.flush()
    summary = {
        "event": "fresh_model_quality_gate",
        "passed": bool(quality.get("passed")),
        "reason": quality.get("reason"),
        "optimizer_started": False,
        "calibration_design": design,
        "calibration_replay_attempt_count": len(attempts),
        "calibration_replay_budget": 15,
        "calibration_budget_excludes_fresh_baseline": True,
        "usable_sample_count_including_baseline": 1 + len(validation_rows),
        "thresholds": v1._json_value(asdict(thresholds)),
        "selected_quality": v1._json_value(quality),
        "calibration_replays": v1._json_value(attempts),
        "calibration_jsonl": str(log_path),
        "preflight_soft_start": not bool(quality.get("passed")),
        "preflight_soft_start_reason": (
            None if quality.get("passed") else quality.get("reason")
        ),
        "mysql_effective_value_canonicalization": True,
        "frozen_design_override_consumed": True,
    }
    quality_path = scenario_dir / "fresh_model_quality_gate.json"
    temporary = quality_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(v1._json_value(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(quality_path)
    return summary
