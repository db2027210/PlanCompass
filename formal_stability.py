"""Windowed stability evaluation from the frozen measurement protocol."""

from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
from typing import Any, Mapping, Sequence


RESOURCE_KEYS = ("cpu", "memory", "io")


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def _cv(values: Sequence[float]) -> float | None:
    if not values:
        return None
    mean = statistics.fmean(values)
    if mean <= 0.0:
        return None
    return statistics.pstdev(values) / mean


@dataclass(frozen=True)
class StabilitySample:
    elapsed_s: float
    cpu: float
    memory: float
    io: float
    qps: float | None = None
    xap_qps: float | None = None
    xtp_tps: float | None = None

    def __post_init__(self) -> None:
        if _finite(self.elapsed_s, "elapsed_s") <= 0.0:
            raise ValueError("elapsed_s must be positive")
        for key in RESOURCE_KEYS:
            value = _finite(getattr(self, key), key)
            if value < 0.0:
                raise ValueError(f"{key} utilization cannot be negative")
        for key in ("qps", "xap_qps", "xtp_tps"):
            value = getattr(self, key)
            if value is not None and _finite(value, key) < 0.0:
                raise ValueError(f"{key} cannot be negative")


@dataclass(frozen=True)
class WindowResult:
    index: int
    start_s: int
    end_s: int
    sample_count: int
    sample_coverage: float
    stable: bool
    failures: tuple[str, ...]
    metrics: Mapping[str, Mapping[str, float | str | bool | None]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start_s": self.start_s,
            "end_s": self.end_s,
            "sample_count": self.sample_count,
            "sample_coverage": self.sample_coverage,
            "stable": self.stable,
            "failures": list(self.failures),
            "metrics": {key: dict(value) for key, value in self.metrics.items()},
        }


@dataclass(frozen=True)
class StabilityDecision:
    calculation: str
    stable: bool
    may_stop: bool
    terminal_at_maximum: bool
    measurement_seconds: float
    sample_coverage: float
    consecutive_stable_windows: int
    required_consecutive_windows: int
    stop_reason: str | None
    windows: tuple[WindowResult, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "calculation": self.calculation,
            "stable": self.stable,
            "may_stop": self.may_stop,
            "terminal_at_maximum": self.terminal_at_maximum,
            "measurement_seconds": self.measurement_seconds,
            "sample_coverage": self.sample_coverage,
            "consecutive_stable_windows": self.consecutive_stable_windows,
            "required_consecutive_windows": self.required_consecutive_windows,
            "stop_reason": self.stop_reason,
            "windows": [window.as_dict() for window in self.windows],
        }


class FrozenStabilityEvaluator:
    def __init__(self, measurement_protocol: Mapping[str, Any]) -> None:
        timing = measurement_protocol.get("timing")
        stability = measurement_protocol.get("stability")
        if measurement_protocol.get("immutable") is not True:
            raise ValueError("measurement protocol must be immutable")
        if not isinstance(timing, Mapping) or not isinstance(stability, Mapping):
            raise ValueError("measurement protocol is missing timing/stability")
        if timing.get("sample_interval_seconds") != 1:
            raise ValueError("only the frozen one-second sample interval is supported")
        self.calculation = str(stability.get("calculation"))
        if self.calculation not in {
            "within_each_window_from_one_second_samples",
            "cv_across_latest_six_window_means",
        }:
            raise ValueError("unsupported stability calculation")
        decision_resources = measurement_protocol.get(
            "stability_decision_resources"
        )
        if decision_resources is None:
            if stability.get("all_cpu_memory_io_resources_must_be_stable") is not True:
                raise ValueError("all-resource stability requirement is missing")
            self.decision_resources = RESOURCE_KEYS
        else:
            if (
                not isinstance(decision_resources, list)
                or len(decision_resources) != 1
                or decision_resources[0] not in RESOURCE_KEYS
            ):
                raise ValueError(
                    "stability_decision_resources must name exactly one resource"
                )
            self.decision_resources = tuple(decision_resources)
        self.window_seconds = int(timing["stability_window_seconds"])
        self.required_windows = int(timing["required_consecutive_stability_windows"])
        self.minimum_seconds = int(timing["minimum_measurement_seconds"])
        self.maximum_seconds = int(timing["maximum_measurement_seconds"])
        self.coverage_minimum = float(timing["sample_coverage_min"])
        self.resource_cv_limits = {
            "cpu": float(stability["cpu_cv_max"]),
            "memory": float(stability["memory_cv_max"]),
            "io": float(stability["io_cv_max"]),
        }
        self.normal_throughput_cv = float(stability["normal_throughput_cv_max"])
        self.xp_cv_limits = {
            "xap_qps": float(stability["xp_xap_qps_cv_max"]),
            "xtp_tps": float(stability["xp_xtp_tps_cv_max"]),
        }
        self.low_utilization_threshold = float(stability["low_utilization_ratio_threshold"])
        self.low_utilization_range_maximum = float(
            stability["low_utilization_absolute_range_max"]
        )
        if (
            self.window_seconds != 30
            or self.minimum_seconds != 180
            or self.maximum_seconds < 600
            or self.maximum_seconds % 30
        ):
            raise ValueError(
                "timing values differ from the frozen 30/180/N protocol "
                "(N must be a multiple of 30 and at least 600)"
            )
        if self.required_windows != 6:
            raise ValueError("required consecutive window count differs from frozen protocol")

    def _resource_metric(self, key: str, values: Sequence[float]) -> dict[str, Any]:
        mean = statistics.fmean(values)
        value_range = max(values) - min(values)
        cv = _cv(values)
        if mean < self.low_utilization_threshold:
            stable = value_range <= self.low_utilization_range_maximum
            rule = "low_utilization_absolute_range"
        else:
            stable = cv is not None and cv <= self.resource_cv_limits[key]
            rule = "coefficient_of_variation"
        return {
            "mean": mean,
            "cv": cv,
            "range": value_range,
            "rule": rule,
            "limit": (
                self.low_utilization_range_maximum
                if rule == "low_utilization_absolute_range"
                else self.resource_cv_limits[key]
            ),
            "stable": stable,
        }

    @staticmethod
    def _throughput_metric(values: Sequence[float], limit: float) -> dict[str, Any]:
        mean = statistics.fmean(values)
        cv = _cv(values)
        stable = mean > 0.0 and cv is not None and cv <= limit
        return {"mean": mean, "cv": cv, "limit": limit, "stable": stable}

    def _window(
        self,
        index: int,
        samples: Sequence[StabilitySample],
        *,
        workload_is_xp: bool,
    ) -> WindowResult:
        coverage = min(1.0, len(samples) / float(self.window_seconds))
        failures: list[str] = []
        metrics: dict[str, Mapping[str, Any]] = {}
        if coverage < self.coverage_minimum:
            failures.append("sample_coverage_below_minimum")
        for key in RESOURCE_KEYS:
            metric = self._resource_metric(key, [float(getattr(sample, key)) for sample in samples])
            metrics[key] = metric
            if key in self.decision_resources and not metric["stable"]:
                failures.append(f"{key}_unstable")
        # Throughput is retained as audit evidence only.  Formal stability is
        # decided exclusively by CPU/Memory/IO plus resource-sample coverage.
        # QPS/TPS correctness and non-zero completion remain separate hard
        # gates, but their short-window CV must not delay a resource-stable run.
        if workload_is_xp:
            for key, limit in self.xp_cv_limits.items():
                values = [getattr(sample, key) for sample in samples]
                if any(value is None for value in values):
                    metrics[key] = {
                        "mean": None, "cv": None, "limit": limit,
                        "stable": False, "decision_role": "audit_only",
                    }
                else:
                    metric = self._throughput_metric([float(value) for value in values], limit)
                    metrics[key] = {**metric, "decision_role": "audit_only"}
        else:
            values = [sample.qps for sample in samples]
            if any(value is None for value in values):
                metrics["qps"] = {
                    "mean": None,
                    "cv": None,
                    "limit": self.normal_throughput_cv,
                    "stable": False,
                    "decision_role": "audit_only",
                }
            else:
                metric = self._throughput_metric(
                    [float(value) for value in values], self.normal_throughput_cv
                )
                metrics["qps"] = {**metric, "decision_role": "audit_only"}
        return WindowResult(
            index=index,
            start_s=(index - 1) * self.window_seconds,
            end_s=index * self.window_seconds,
            sample_count=len(samples),
            sample_coverage=coverage,
            stable=not failures,
            failures=tuple(failures),
            metrics=metrics,
        )

    def evaluate(
        self,
        samples: Sequence[StabilitySample],
        *,
        workload_is_xp: bool,
        elapsed_s: float | None = None,
    ) -> StabilityDecision:
        if not samples:
            raise ValueError("stability evaluation requires samples")
        ordered = sorted(samples, key=lambda sample: sample.elapsed_s)
        if any(right.elapsed_s <= left.elapsed_s for left, right in zip(ordered, ordered[1:])):
            raise ValueError("sample elapsed_s values must be strictly increasing")
        measurement_seconds = (
            _finite(elapsed_s, "elapsed_s") if elapsed_s is not None else ordered[-1].elapsed_s
        )
        if measurement_seconds < ordered[-1].elapsed_s:
            raise ValueError("elapsed_s precedes the final sample")
        completed_window_count = int(measurement_seconds // self.window_seconds)
        windows: list[WindowResult] = []
        for zero_based_index in range(completed_window_count):
            index = zero_based_index + 1
            start = zero_based_index * self.window_seconds
            end = index * self.window_seconds
            current = [
                sample
                for sample in ordered
                if sample.elapsed_s > start and sample.elapsed_s <= end
            ]
            if current:
                windows.append(self._window(index, current, workload_is_xp=workload_is_xp))
            else:
                windows.append(
                    WindowResult(
                        index=index,
                        start_s=start,
                        end_s=end,
                        sample_count=0,
                        sample_coverage=0.0,
                        stable=False,
                        failures=("sample_coverage_below_minimum",),
                        metrics={},
                    )
                )
        consecutive = 0
        for window in reversed(windows):
            if not window.stable:
                break
            consecutive += 1
        decision_window_seconds = self.window_seconds * self.required_windows
        decision_start = max(0.0, measurement_seconds - decision_window_seconds)
        decision_samples = [
            sample for sample in ordered
            if sample.elapsed_s > decision_start and sample.elapsed_s <= measurement_seconds
        ]
        overall_coverage = min(
            1.0,
            len(decision_samples) / float(decision_window_seconds),
        )
        enough_time = measurement_seconds >= self.minimum_seconds
        if (
            self.calculation == "cv_across_latest_six_window_means"
            and len(windows) >= self.required_windows
        ):
            latest = windows[-self.required_windows:]
            coverage_ok = all(
                window.sample_coverage >= self.coverage_minimum
                for window in latest
            )
            resource_means_stable = coverage_ok
            for key in self.decision_resources:
                values = [
                    float(window.metrics[key]["mean"])
                    for window in latest
                    if key in window.metrics
                ]
                if len(values) != self.required_windows:
                    resource_means_stable = False
                    break
                mean = statistics.fmean(values)
                if mean < self.low_utilization_threshold:
                    resource_stable = (
                        max(values) - min(values)
                        <= self.low_utilization_range_maximum
                    )
                else:
                    cv = _cv(values)
                    resource_stable = (
                        cv is not None and cv <= self.resource_cv_limits[key]
                    )
                if not resource_stable:
                    resource_means_stable = False
                    break
            consecutive = self.required_windows if resource_means_stable else 0
        enough_windows = consecutive >= self.required_windows
        stable = enough_time and enough_windows and overall_coverage >= self.coverage_minimum
        may_stop = stable and measurement_seconds <= self.maximum_seconds
        terminal_at_maximum = measurement_seconds >= self.maximum_seconds
        if may_stop:
            reason = (
                "latest_six_window_means_stable"
                if self.calculation == "cv_across_latest_six_window_means"
                else "six_consecutive_stable_windows"
            )
        elif terminal_at_maximum:
            reason = "maximum_measurement_seconds_reached_unstable"
        else:
            reason = None
        return StabilityDecision(
            calculation=self.calculation,
            stable=stable,
            may_stop=may_stop,
            terminal_at_maximum=terminal_at_maximum,
            measurement_seconds=measurement_seconds,
            sample_coverage=overall_coverage,
            consecutive_stable_windows=consecutive,
            required_consecutive_windows=self.required_windows,
            stop_reason=reason,
            windows=tuple(windows),
        )
