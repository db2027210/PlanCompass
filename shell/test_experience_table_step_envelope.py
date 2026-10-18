#!/usr/bin/env python3
"""Fail-closed audit of the experience-table SGD first-step envelope."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gradient_descent_optimizer import (  # noqa: E402
    GradientDescentParameterOptimizer,
    ParameterRange,
)
from formal_contracts import resolve_target_relative_scoring  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def close(actual: float, expected: float, label: str) -> None:
    require(
        math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12),
        f"{label} mismatch: actual={actual!r}, expected={expected!r}",
    )


def audit(config_path: Path) -> Dict[str, Any]:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    attempt_contract = (
        cfg.get("_attempt22_contract")
        or cfg.get("_attempt21_contract")
        or cfg.get("_attempt20_contract")
        or cfg.get("_attempt19_contract")
        or cfg.get("_attempt18_contract")
        or cfg.get("_attempt17_contract")
        or cfg.get("_attempt16_contract")
        or cfg.get("_attempt15_contract")
    )
    range_contract = (
        attempt_contract.get("parameter_range_contract")
        if isinstance(attempt_contract, dict)
        else None
    )
    range_source_path = None
    if isinstance(range_contract, dict):
        range_source_path = Path(str(range_contract["source"]))
        range_source_bytes = range_source_path.read_bytes()
        require(
            hashlib.sha256(range_source_bytes).hexdigest()
            == range_contract["source_sha256"],
            "parameter-range source SHA256 mismatch",
        )
        range_source = json.loads(range_source_bytes.decode("utf-8"))
        for name, expected_bounds in range_contract["active_ranges"].items():
            documented_bounds = [float(value) for value in range_source["bounds"][name]]
            configured = cfg["param_ranges"][name]
            configured_bounds = [
                float(configured["min_value"]),
                float(configured["max_value"]),
            ]
            require(documented_bounds == expected_bounds, f"{name} range contract/source mismatch")
            require(configured_bounds == expected_bounds, f"{name} configured range mismatch")
        for name, expected_bounds in range_contract.get("fixed_ranges", {}).items():
            configured = cfg["param_ranges"][name]
            configured_bounds = [
                float(configured["min_value"]),
                float(configured["max_value"]),
            ]
            require(configured_bounds == expected_bounds, f"{name} fixed range mismatch")
    envelope = cfg.get("_experience_table_step_envelope")
    require(isinstance(envelope, dict), "step-envelope contract missing")
    require(envelope.get("required_before_launch") is True, "step-envelope must be required")

    evidence_path = Path(str(envelope["resource_evidence"]))
    evidence_bytes = evidence_path.read_bytes()
    require(
        hashlib.sha256(evidence_bytes).hexdigest()
        == envelope["resource_evidence_sha256"],
        "step-envelope resource evidence SHA256 mismatch",
    )
    evidence = json.loads(evidence_bytes.decode("utf-8"))
    capability = {name: float(value) for name, value in cfg["capability"].items()}
    if isinstance(evidence.get("formal_pg_resource"), dict):
        aggregate = evidence["formal_pg_resource"]["aggregate"]
        resource_now = {
            "cpu": float(aggregate["cpu_utilization"]) * capability["cpu"],
            "memory": float(aggregate["memory_utilization"]) * capability["memory"],
            "io": float(aggregate["total_bps"]) / 1048576.0,
        }
    else:
        source_capability = envelope.get("resource_evidence_capability")
        utilization = evidence.get("resource_utilization")
        require(isinstance(source_capability, dict), "resource evidence capability missing")
        require(isinstance(utilization, dict), "resource utilization evidence missing")
        resource_now = {
            name: float(utilization[name]) * float(source_capability[name])
            for name in ("cpu", "memory", "io")
        }
    gradient_cfg = cfg["optimizer"]["gradient"]
    targets = gradient_cfg.get("target_bottleneck_resources") or []
    if isinstance(targets, str):
        targets = [item.strip() for item in targets.split(",") if item.strip()]
    scoring = resolve_target_relative_scoring(
        cfg["scoring"],
        str(targets[0]) if len(targets) == 1 else "",
    )
    optimizer = GradientDescentParameterOptimizer(
        resource_estimation_func=lambda _sql, _params: resource_now,
        capability=capability,
        reward_type="sigmoid_linear",
        penalty_type="sigmoid_linear",
        overload_threshold=float(scoring["overload_threshold"]),
        score_mode=str(scoring["score_mode"]),
        resource_weights=scoring.get("resource_weights"),
        resource_reward_weights=scoring.get("resource_reward_weights"),
        resource_penalty_weights=scoring.get("resource_penalty_weights"),
        resource_target_ranges=scoring.get("resource_target_ranges"),
        verbose=False,
    )

    eta = float(gradient_cfg["eta"])
    close(eta, float(envelope["eta"]), "eta")
    close(float(gradient_cfg["max_eta"]), eta, "max_eta")
    max_relative_limit = float(envelope["max_allowed_relative_first_step"])
    expected_directions = envelope["expected_directions"]
    expected_first_step = envelope["expected_unclipped_first_step"]
    initial_params = cfg["initial_params"]
    param_ranges = cfg["param_ranges"]
    table = cfg["sensitivity_table"]
    active_params = gradient_cfg["active_params"]

    steps: Dict[str, Any] = {}
    max_abs_direction = 0.0
    max_relative_step = 0.0
    clipped_params = []
    for name in active_params:
        direction, _ = optimizer._calculate_direction(
            name,
            table,
            resource_now,
            capability,
        )
        close(direction, float(expected_directions[name]), f"{name}.direction")
        old_value = float(initial_params[name])
        raw_value = old_value * (1.0 + eta * direction)
        close(raw_value, float(expected_first_step[name]), f"{name}.raw_value")
        raw_range = param_ranges[name]
        param_range = ParameterRange(
            min_value=float(raw_range["min_value"]),
            max_value=float(raw_range["max_value"]),
        )
        new_value = optimizer._clip(raw_value, param_range)
        clipped = not math.isclose(raw_value, new_value, rel_tol=0.0, abs_tol=1e-15)
        if clipped:
            clipped_params.append(name)
        relative_step = abs(eta * direction)
        max_abs_direction = max(max_abs_direction, abs(direction))
        max_relative_step = max(max_relative_step, relative_step)
        steps[name] = {
            "old_value": old_value,
            "direction": direction,
            "eta_times_direction": eta * direction,
            "relative_step": relative_step,
            "raw_value": raw_value,
            "new_value": new_value,
            "clipped": clipped,
            "min_value": param_range.min_value,
            "max_value": param_range.max_value,
        }

    close(max_abs_direction, float(envelope["maximum_absolute_direction"]), "max direction")
    close(max_relative_step, float(envelope["maximum_relative_first_step"]), "max step")
    require(max_relative_step <= max_relative_limit, "first step exceeds relative envelope")
    require(not clipped_params, f"first step clips parameters: {clipped_params}")
    require(envelope.get("all_first_step_values_inside_ranges") is True, "envelope range claim false")

    return {
        "status": "passed",
        "contract_id": envelope["contract_id"],
        "config": str(config_path),
        "resource_evidence": str(evidence_path),
        "parameter_range_source": str(range_source_path) if range_source_path else None,
        "parameter_ranges": (
            range_contract["active_ranges"] if isinstance(range_contract, dict) else {
                name: [float(item["min_value"]), float(item["max_value"])]
                for name, item in cfg["param_ranges"].items()
            }
        ),
        "fixed_ranges": (
            range_contract.get("fixed_ranges", {}) if isinstance(range_contract, dict) else {}
        ),
        "projected_resource": resource_now,
        "projected_utilization": {
            name: resource_now[name] / capability[name] for name in resource_now
        },
        "eta": eta,
        "max_absolute_direction": max_abs_direction,
        "max_relative_first_step": max_relative_step,
        "max_allowed_relative_first_step": max_relative_limit,
        "clipped_params": clipped_params,
        "steps": steps,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.config), sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
