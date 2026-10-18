"""Pure fail-closed evaluator for frozen default-baseline resource gates."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

from formal_acceptance_policy import LivePoint


class FrozenBaselineGateError(RuntimeError):
    """Raised when baseline evidence cannot be interpreted safely."""


@dataclass(frozen=True)
class PressureEvidence:
    memory_psi_active: bool = False
    major_faults_observed: bool = False
    working_set_refaults_observed: bool = False
    cache_evictions_observed: bool = False
    temp_or_disk_spill_observed: bool = False
    io_throttle_or_wait_observed: bool = False
    xap_completed: int = 0
    at_transactions_completed: int = 0
    container_pid_unchanged: bool = True
    database_process_unchanged: bool = True
    io_limit_unchanged: bool = True


@dataclass(frozen=True)
class BaselineGateDecision:
    passed: bool
    scenario: str
    failures: tuple[str, ...]
    memory_pressure_passed: bool | None
    io_pressure_passed: bool | None


def _load_protocol(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FrozenBaselineGateError(f"cannot load measurement protocol: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("immutable") is not True:
        raise FrozenBaselineGateError("measurement protocol must be immutable")
    if payload.get("config_id") != "measurement_protocol_20260721_v3":
        raise FrozenBaselineGateError("unexpected measurement protocol config_id")
    baseline = payload.get("baseline_gates")
    none = payload.get("none_gates")
    if not isinstance(baseline, dict) or not isinstance(none, dict):
        raise FrozenBaselineGateError("measurement protocol lacks baseline/none gates")
    required = {
        "target_resource_utilization_min": 0.85,
        "non_target_resource_utilization_max": 0.8,
        "error_rate_max": 0.01,
        "memory_pressure_evidence_required": True,
        "io_pressure_evidence_required": True,
        "no_oom_or_database_restart": True,
    }
    none_required = {
        "cpu_memory_io_utilization_max": 0.75,
        "error_rate_max": 0.01,
        "no_oom_or_database_restart": True,
    }
    if any(baseline.get(key) != value for key, value in required.items()):
        raise FrozenBaselineGateError("frozen baseline gate values differ")
    if any(none.get(key) != value for key, value in none_required.items()):
        raise FrozenBaselineGateError("frozen none gate values differ")
    return baseline, none


def _validate_point(point: LivePoint, *, xp: bool) -> None:
    for flag in (
        point.correctness_passed,
        point.stable,
        point.oom,
        point.database_restarted,
    ):
        if type(flag) is not bool:
            raise FrozenBaselineGateError("baseline flags must be boolean")
    for name in (
        "sample_coverage",
        "error_rate",
        "cpu_utilization",
        "memory_utilization",
        "io_utilization",
    ):
        value = getattr(point, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FrozenBaselineGateError(f"{name} must be numeric")
        if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
            raise FrozenBaselineGateError(f"{name} must be a finite [0,1] ratio")
    if xp:
        if point.qps is not None or point.xap_qps is None or point.xtp_tps is None:
            raise FrozenBaselineGateError("XP baseline must contain exactly two stream metrics")
    else:
        if point.qps is None or point.xap_qps is not None or point.xtp_tps is not None:
            raise FrozenBaselineGateError("normal baseline must contain only qps")


def evaluate_default_baseline(
    protocol_path: Path,
    point: LivePoint,
    pressure: PressureEvidence,
    *,
    scenario: str,
    xp: bool,
) -> BaselineGateDecision:
    """Evaluate only the default baseline; these gates never apply to candidates."""

    baseline, none = _load_protocol(protocol_path)
    if scenario not in {"memory", "io", "none"}:
        raise FrozenBaselineGateError("scenario must be memory, io, or none")
    if type(pressure.xap_completed) is not int or pressure.xap_completed < 0:
        raise FrozenBaselineGateError("xap_completed must be a non-negative integer")
    if type(pressure.at_transactions_completed) is not int or pressure.at_transactions_completed < 0:
        raise FrozenBaselineGateError("at_transactions_completed must be a non-negative integer")
    _validate_point(point, xp=xp)

    failures: list[str] = []
    if not point.correctness_passed:
        failures.append("correctness_failed")
    if not point.stable:
        failures.append("stability_failed")
    if point.sample_coverage < 0.9:
        failures.append("sample_coverage_below_minimum")
    error_max = none["error_rate_max"] if scenario == "none" else baseline["error_rate_max"]
    if point.error_rate > error_max:
        failures.append("error_rate_above_maximum")
    if point.oom:
        failures.append("oom")
    if point.database_restarted:
        failures.append("database_restarted")

    resources = {
        "cpu": point.cpu_utilization,
        "memory": point.memory_utilization,
        "io": point.io_utilization,
    }
    memory_pressure: bool | None = None
    io_pressure: bool | None = None
    if scenario == "none":
        if any(value > none["cpu_memory_io_utilization_max"] for value in resources.values()):
            failures.append("none_resource_utilization_above_maximum")
    else:
        target = resources[scenario]
        if target < baseline["target_resource_utilization_min"]:
            failures.append("target_resource_utilization_below_minimum")
        if any(
            value > baseline["non_target_resource_utilization_max"]
            for name, value in resources.items()
            if name != scenario
        ):
            failures.append("non_target_resource_utilization_above_maximum")
        if scenario == "memory":
            memory_pressure = any(
                (
                    pressure.memory_psi_active,
                    pressure.major_faults_observed,
                    pressure.working_set_refaults_observed,
                    pressure.cache_evictions_observed,
                    pressure.temp_or_disk_spill_observed,
                )
            )
            if not memory_pressure:
                failures.append("memory_pressure_evidence_absent")
            if point.memory_utilization < max(point.cpu_utilization, point.io_utilization):
                failures.append("memory_pressure_not_dominant")
        else:
            io_pressure = (
                pressure.io_throttle_or_wait_observed
                and pressure.xap_completed >= 1
                and pressure.at_transactions_completed >= 1
                and pressure.container_pid_unchanged
                and pressure.database_process_unchanged
                and pressure.io_limit_unchanged
            )
            if not xp:
                failures.append("io_baseline_requires_xp")
            if not io_pressure:
                failures.append("io_pressure_evidence_incomplete")

    return BaselineGateDecision(
        passed=not failures,
        scenario=scenario,
        failures=tuple(failures),
        memory_pressure_passed=memory_pressure,
        io_pressure_passed=io_pressure,
    )
