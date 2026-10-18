"""Strict measurement-window metrics for the frozen MySQL random-walk runs.

The shared preflight runner emits cumulative workload counters.  The frozen
stability protocol, however, is defined over one-second samples.  This module
turns cumulative counters or timestamped completion events into interval rates
without admitting warm-up or post-deadline completions.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
from typing import Any, Mapping, Sequence

from formal_acceptance_policy import LivePoint
from formal_stability import FrozenStabilityEvaluator, StabilityDecision, StabilitySample


class MeasurementMetricError(RuntimeError):
    """Raw measurement evidence is incomplete, non-causal, or non-finite."""


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MeasurementMetricError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise MeasurementMetricError(f"{label} must be finite")
    return result


def _strict_epochs(sample_epochs_ms: Sequence[int], start_ms: int, end_ms: int) -> tuple[int, ...]:
    if isinstance(start_ms, bool) or isinstance(end_ms, bool) or not isinstance(start_ms, int) or not isinstance(end_ms, int):
        raise MeasurementMetricError("measurement boundaries must be integer epoch milliseconds")
    if start_ms >= end_ms:
        raise MeasurementMetricError("measurement boundary is empty")
    epochs = tuple(int(value) for value in sample_epochs_ms)
    if not epochs or any(right <= left for left, right in zip(epochs, epochs[1:])):
        raise MeasurementMetricError("sample epochs must be non-empty and strictly increasing")
    if epochs[0] <= start_ms or epochs[-1] > end_ms + 1500:
        raise MeasurementMetricError("sample epochs fall outside the measurement interval")
    return epochs


def event_interval_rates(
    sample_epochs_ms: Sequence[int],
    completion_epochs_ms: Sequence[int],
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[float, ...]:
    """Return per-sample completion rates, excluding warm-up and late events."""

    epochs = _strict_epochs(sample_epochs_ms, start_ms, end_ms)
    completions = sorted(int(value) for value in completion_epochs_ms)
    if any(value <= start_ms or value > end_ms for value in completions):
        raise MeasurementMetricError("completion list contains a warm-up or post-deadline event")
    result: list[float] = []
    position = 0
    previous_epoch = start_ms
    for raw_epoch in epochs:
        epoch = min(raw_epoch, end_ms)
        count = 0
        while position < len(completions) and completions[position] <= epoch:
            if completions[position] > previous_epoch:
                count += 1
            position += 1
        seconds = (epoch - previous_epoch) / 1000.0
        if seconds <= 0.0:
            raise MeasurementMetricError("non-positive sampling interval")
        result.append(count / seconds)
        previous_epoch = epoch
    if position != len(completions):
        raise MeasurementMetricError("sample series does not cover all in-window completions")
    return tuple(result)


def cumulative_interval_rates(
    sample_epochs_ms: Sequence[int],
    cumulative_observations: Sequence[tuple[int, float]],
    *,
    start_ms: int,
    end_ms: int,
    baseline_count: float = 0.0,
) -> tuple[float, ...]:
    """Convert a monotonic cumulative progress counter to one-second rates."""

    epochs = _strict_epochs(sample_epochs_ms, start_ms, end_ms)
    observations = sorted(
        (int(epoch), _number(value, "cumulative counter"))
        for epoch, value in cumulative_observations
        if start_ms <= int(epoch) <= end_ms
    )
    if not observations:
        raise MeasurementMetricError("no cumulative workload observations in measurement")
    previous_observed = _number(baseline_count, "baseline counter")
    if previous_observed < 0.0:
        raise MeasurementMetricError("baseline counter cannot be negative")
    for _, value in observations:
        if value < previous_observed:
            raise MeasurementMetricError("cumulative workload counter regressed")
        previous_observed = value

    result: list[float] = []
    observation_index = 0
    cumulative = float(baseline_count)
    previous_cumulative = cumulative
    previous_epoch = start_ms
    for raw_epoch in epochs:
        epoch = min(raw_epoch, end_ms)
        while observation_index < len(observations) and observations[observation_index][0] <= epoch:
            cumulative = observations[observation_index][1]
            observation_index += 1
        seconds = (epoch - previous_epoch) / 1000.0
        if seconds <= 0.0:
            raise MeasurementMetricError("non-positive sampling interval")
        delta = cumulative - previous_cumulative
        if delta < 0.0:
            raise MeasurementMetricError("cumulative workload counter regressed between samples")
        result.append(delta / seconds)
        previous_cumulative = cumulative
        previous_epoch = epoch
    if observation_index != len(observations):
        raise MeasurementMetricError("sample series does not cover cumulative observations")
    return tuple(result)


@dataclass(frozen=True)
class PointMetricSummary:
    live_point: LivePoint
    stability: StabilityDecision
    measurement_seconds: float
    throughput_samples: tuple[float, ...]
    xap_samples: tuple[float, ...] | None
    xtp_samples: tuple[float, ...] | None


def summarize_live_point(
    measurement_protocol: Mapping[str, Any],
    resource_samples: Sequence[Mapping[str, Any]],
    *,
    qps_samples: Sequence[float] | None,
    xap_samples: Sequence[float] | None,
    xtp_samples: Sequence[float] | None,
    latency_p95_ms: float,
    latency_p99_ms: float,
    correctness_passed: bool,
    error_rate: float,
    oom: bool,
    database_restarted: bool,
) -> PointMetricSummary:
    """Create the sole LivePoint representation used by frozen Outer policy."""

    xp = xap_samples is not None or xtp_samples is not None
    if xp:
        if qps_samples is not None or xap_samples is None or xtp_samples is None:
            raise MeasurementMetricError("XP requires exactly the two stream sample series")
        if len(xap_samples) != len(resource_samples) or len(xtp_samples) != len(resource_samples):
            raise MeasurementMetricError("XP throughput/resource sample counts differ")
    else:
        if qps_samples is None or len(qps_samples) != len(resource_samples):
            raise MeasurementMetricError("normal throughput/resource sample counts differ")
    if not resource_samples:
        raise MeasurementMetricError("resource sample series is empty")

    first_elapsed = _number(resource_samples[0]["elapsed_s"], "elapsed_s")
    normalized: list[StabilitySample] = []
    for index, row in enumerate(resource_samples):
        elapsed = _number(row["elapsed_s"], "elapsed_s") - first_elapsed + 1.0
        normalized.append(
            StabilitySample(
                elapsed_s=elapsed,
                cpu=_number(row["cpu_utilization_ratio"], "cpu_utilization_ratio"),
                memory=_number(row["memory_utilization_ratio"], "memory_utilization_ratio"),
                io=_number(row["io_utilization_ratio"], "io_utilization_ratio"),
                qps=None if xp else _number(qps_samples[index], "qps sample"),  # type: ignore[index]
                xap_qps=_number(xap_samples[index], "xap sample") if xp else None,  # type: ignore[index]
                xtp_tps=_number(xtp_samples[index], "xtp sample") if xp else None,  # type: ignore[index]
            )
        )
    elapsed = normalized[-1].elapsed_s
    evaluator = FrozenStabilityEvaluator(measurement_protocol)
    stability = evaluator.evaluate(normalized, workload_is_xp=xp, elapsed_s=elapsed)

    coverage = min(1.0, len(resource_samples) / max(elapsed, 1.0))
    # Raw one-second evidence remains unchanged in the point artifacts.  The
    # frozen summary contract expresses utilization as a bounded [0,1] ratio,
    # so cap only the aggregate policy inputs against scheduler/cgroup jitter.
    cpu = min(1.0, statistics.fmean(sample.cpu for sample in normalized))
    memory = min(1.0, statistics.fmean(sample.memory for sample in normalized))
    io = min(1.0, statistics.fmean(sample.io for sample in normalized))
    p95 = _number(latency_p95_ms, "latency_p95_ms")
    p99 = _number(latency_p99_ms, "latency_p99_ms")
    errors = _number(error_rate, "error_rate")
    if p95 <= 0.0 or p99 < p95 or not 0.0 <= errors <= 1.0:
        raise MeasurementMetricError("invalid latency or error-rate summary")
    if any(not 0.0 <= value <= 1.0 for value in (coverage, cpu, memory, io)):
        raise MeasurementMetricError("coverage/resource utilization is outside [0,1]")

    if xp:
        xap_mean = statistics.fmean(float(value) for value in xap_samples)  # type: ignore[arg-type]
        xtp_mean = statistics.fmean(float(value) for value in xtp_samples)  # type: ignore[arg-type]
        qps_mean = None
    else:
        qps_mean = statistics.fmean(float(value) for value in qps_samples)  # type: ignore[arg-type]
        xap_mean = None
        xtp_mean = None
    point = LivePoint(
        correctness_passed=bool(correctness_passed),
        stable=stability.stable,
        sample_coverage=coverage,
        error_rate=errors,
        oom=bool(oom),
        database_restarted=bool(database_restarted),
        qps=qps_mean,
        xap_qps=xap_mean,
        xtp_tps=xtp_mean,
        latency_p95_ms=p95,
        latency_p99_ms=p99,
        cpu_utilization=cpu,
        memory_utilization=memory,
        io_utilization=io,
    )
    return PointMetricSummary(
        live_point=point,
        stability=stability,
        measurement_seconds=elapsed,
        throughput_samples=tuple(float(value) for value in (xap_samples if xp else qps_samples)),  # type: ignore[arg-type]
        xap_samples=tuple(float(value) for value in xap_samples) if xp else None,  # type: ignore[arg-type]
        xtp_samples=tuple(float(value) for value in xtp_samples) if xp else None,  # type: ignore[arg-type]
    )
