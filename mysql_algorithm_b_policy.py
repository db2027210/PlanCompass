"""MySQL adaptation of the PostgreSQL Resource-Adaptive Algorithm B policy."""

from __future__ import annotations

import copy
from dataclasses import asdict
import itertools
import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

from double_loop_grid_optimizer import DoubleLoopGridOptimizer
from double_loop_search_common import ParameterRange
from double_loop_scenario import (
    FreshExperienceModel,
    FreshModelQualityThresholds,
    measurement_from_payload,
)


def _signature(params: Mapping[str, float]) -> Tuple[Tuple[str, float], ...]:
    return tuple(
        (str(name), round(float(value), 12))
        for name, value in sorted(params.items())
    )


def mysql_fresh_model_calibration_points(
    base_params: Mapping[str, float],
    ranges: Mapping[str, ParameterRange],
    candidates: Sequence[str],
    *,
    replay_budget: int = 15,
) -> List[Dict[str, Any]]:
    """Build 15 distinct effective MySQL points using PG's coordinate-first policy.

    Integer and boolean MySQL variables may have fewer than 15 effective values.
    Single-coordinate neighbours are emitted first; deterministic interactions
    fill only the otherwise unavailable slots.
    """
    budget = int(replay_budget)
    if budget != 15:
        raise ValueError("Algorithm B calibration replay budget must be exactly 15")
    if not candidates:
        raise ValueError("Algorithm B calibration requires active parameters")

    seen = {_signature(base_params)}
    result: List[Dict[str, Any]] = []
    ordered_values: Dict[str, List[float]] = {}

    for name in candidates:
        values = DoubleLoopGridOptimizer.discretize(
            ranges[name],
            include_value=float(base_params[name]),
        )
        base = float(base_params[name])
        alternatives = [
            float(value)
            for value in values
            if not math.isclose(float(value), base, rel_tol=0.0, abs_tol=1e-12)
        ]
        alternatives.sort(
            key=lambda value: (
                abs(math.log(value / base))
                if value > 0.0 and base > 0.0
                else abs(value - base),
                value,
            )
        )
        ordered_values[name] = alternatives

    def add(phase: str, params: Mapping[str, float], changed: Sequence[str]) -> None:
        signature = _signature(params)
        if signature in seen or len(result) >= budget:
            return
        seen.add(signature)
        result.append(
            {
                "calibration_id": f"calibration_{len(result) + 1:03d}",
                "phase": phase,
                "design": "mysql_effective_coordinate_first_v1",
                "params": {name: float(value) for name, value in params.items()},
                "changed_parameters": list(changed),
            }
        )

    depth = 0
    while len(result) < budget:
        added = False
        for name in candidates:
            values = ordered_values[name]
            if depth >= len(values):
                continue
            params = dict(base_params)
            params[name] = values[depth]
            before = len(result)
            add("coordinate_axis_local", params, [name])
            added = added or len(result) > before
            if len(result) == budget:
                return result
        if not added and all(depth + 1 >= len(ordered_values[name]) for name in candidates):
            break
        depth += 1

    representatives = {
        name: values[: min(3, len(values))]
        for name, values in ordered_values.items()
    }
    for size in range(2, len(candidates) + 1):
        for subset in itertools.combinations(candidates, size):
            pools = [representatives[name] for name in subset]
            if any(not pool for pool in pools):
                continue
            for selected in itertools.product(*pools):
                params = dict(base_params)
                params.update(zip(subset, selected))
                add("effective_interaction_fill", params, subset)
                if len(result) == budget:
                    return result

    raise RuntimeError(
        f"unable to construct 15 distinct effective calibration points; got {len(result)}"
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


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
) -> Dict[str, Any]:
    """Run the one-time Top15 model gate without reusing candidate replays."""
    thresholds = thresholds or FreshModelQualityThresholds()
    design = mysql_fresh_model_calibration_points(base_params, ranges, candidates)
    attempts: List[Dict[str, Any]] = []
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
            }
            try:
                raw = validator(sql_set, dict(design_row["params"]))
                measurement = measurement_from_payload(
                    raw.get("resources") or raw.get("resource") or {},
                    raw,
                )
                row = model.build_experience(
                    design_row["params"], measurement.as_resource(), raw
                )
                validation_rows.append(row)
                model.add(design_row["params"], measurement.as_resource(), raw)
                replay.update(
                    {
                        "status": "usable",
                        "measurement": measurement.as_resource(),
                        "real_resource_score": model.objective.resource_score_details(
                            measurement
                        )["total"],
                    }
                )
            except Exception as exc:
                replay.update(
                    {
                        "status": "failed",
                        "failure_reason": f"{type(exc).__name__}: {exc}",
                    }
                )
            attempts.append(copy.deepcopy(replay))
            handle.write(json.dumps(_json_value(replay), ensure_ascii=False) + "\n")
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
        quality_record = {
            "event": "fresh_model_quality_gate_attempt",
            **_json_value(quality),
        }
        handle.write(json.dumps(quality_record, ensure_ascii=False) + "\n")
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
        "thresholds": _json_value(asdict(thresholds)),
        "selected_quality": _json_value(quality),
        "calibration_replays": _json_value(attempts),
        "calibration_jsonl": str(log_path),
        "preflight_soft_start": not bool(quality.get("passed")),
        "preflight_soft_start_reason": (
            None if quality.get("passed") else quality.get("reason")
        ),
        "mysql_effective_value_canonicalization": True,
    }
    quality_path = scenario_dir / "fresh_model_quality_gate.json"
    temporary = quality_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(_json_value(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(quality_path)
    return summary


def preflight_resource_error_bounds(
    quality_gate: Mapping[str, Any],
) -> Dict[str, Dict[str, float]]:
    selected = quality_gate.get("selected_quality")
    selected = selected if isinstance(selected, Mapping) else {}
    evaluation = selected.get("selected_uncertainty_evaluation")
    if not isinstance(evaluation, Mapping):
        evaluation = selected.get("selected_evaluation")
    evaluation = evaluation if isinstance(evaluation, Mapping) else {}
    metrics = evaluation.get("resource_metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    result: Dict[str, Dict[str, float]] = {}
    for name in ("cpu", "memory", "io"):
        item = metrics.get(name)
        if not isinstance(item, Mapping):
            result[name] = {"lower_delta": 0.0, "upper_delta": 0.0, "sample_count": 0}
            continue
        result[name] = {
            "lower_delta": float(item.get("signed_residual_lower", 0.0)),
            "upper_delta": float(item.get("signed_residual_upper", 0.0)),
            "sample_count": max(0, int(item.get("sample_count", 0))),
        }
    return result


def effective_outer_round_budget(
    ranges: Mapping[str, ParameterRange],
    active_params: Sequence[str],
    epsilon: float,
    requested_rounds: int,
) -> int:
    if requested_rounds > 0:
        return int(requested_rounds)
    if not math.isfinite(epsilon) or epsilon <= 0.0:
        raise ValueError("epsilon must be finite and positive")
    required_halvings = 0
    for name in active_params:
        width = max(0.0, ranges[name].width)
        if width > epsilon:
            required_halvings = max(
                required_halvings,
                int(math.ceil(math.log(width / epsilon, 2))),
            )
    return max(1, 2 * max(1, required_halvings))
