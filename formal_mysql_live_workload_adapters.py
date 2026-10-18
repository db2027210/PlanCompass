"""Workload-native live evidence adapters for formal MySQL measurements.

This module is intentionally independent from the campaign runner.  It parses
only client-produced completion/progress evidence; MySQL ``Questions`` and
other server activity counters are never accepted as workload throughput.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence


class LiveWorkloadEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class NativeProgress:
    epoch_ms: int
    primary_total: int
    secondary_total: int | None = None


@dataclass(frozen=True)
class NativeInterval:
    end_epoch_ms: int
    primary_rate: float
    secondary_rate: float | None = None


_HYBENCH_MARKER = re.compile(r"HYBENCH_FORMAL_SAMPLE_JSON=(\{[^\n]+\})")
_BENCHBASE_MARKER = re.compile(r"BENCHBASE_FORMAL_SAMPLE_JSON=(\{[^\n]+\})")


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiveWorkloadEvidenceError(f"{label} must be an integer")
    parsed = int(value)
    if float(value) != parsed or parsed < 0:
        raise LiveWorkloadEvidenceError(f"{label} must be a non-negative integer")
    return parsed


def _progress_from_payloads(
    payloads: Iterable[Mapping[str, Any]], *, primary_key: str, secondary_key: str | None,
) -> tuple[NativeProgress, ...]:
    result = tuple(
        NativeProgress(
            epoch_ms=_integer(row.get("epoch_ms"), "epoch_ms"),
            primary_total=_integer(row.get(primary_key), primary_key),
            secondary_total=(
                _integer(row.get(secondary_key), secondary_key)
                if secondary_key is not None else None
            ),
        )
        for row in payloads
    )
    if len(result) < 2:
        raise LiveWorkloadEvidenceError("at least two native progress samples are required")
    for left, right in zip(result, result[1:]):
        if right.epoch_ms <= left.epoch_ms:
            raise LiveWorkloadEvidenceError("native progress epochs must increase")
        if right.primary_total < left.primary_total:
            raise LiveWorkloadEvidenceError("native primary completion counter regressed")
        if (
            left.secondary_total is not None
            and right.secondary_total is not None
            and right.secondary_total < left.secondary_total
        ):
            raise LiveWorkloadEvidenceError("native secondary completion counter regressed")
    return result


def parse_hybench_progress(stdout_text: str) -> tuple[NativeProgress, ...]:
    """Parse AP or XP progress emitted once per second by HyBenchFormalMain."""
    rows = [json.loads(match) for match in _HYBENCH_MARKER.findall(stdout_text)]
    measured = [row for row in rows if row.get("measurement_phase") is True]
    return _progress_from_payloads(
        measured, primary_key="xap_or_ap_total", secondary_key="xtp_total"
    )


def parse_benchbase_formal_progress(stdout_text: str) -> tuple[NativeProgress, ...]:
    """Parse the required TPC-H/TPC-C formal marker.

    Stock BenchBase's human log line (``Throughput: ... txn/sec``) has neither
    an epoch nor a cumulative count and is therefore intentionally rejected.
    The launcher must emit BENCHBASE_FORMAL_SAMPLE_JSON with ``epoch_ms`` and
    ``completed_total`` from the worker completion path.
    """
    rows = [json.loads(match) for match in _BENCHBASE_MARKER.findall(stdout_text)]
    return _progress_from_payloads(
        rows, primary_key="completed_total", secondary_key=None
    )


def progress_interval_rates(
    progress: Sequence[NativeProgress],
) -> tuple[NativeInterval, ...]:
    if len(progress) < 2:
        raise LiveWorkloadEvidenceError("at least two progress samples are required")
    result: list[NativeInterval] = []
    for left, right in zip(progress, progress[1:]):
        seconds = (right.epoch_ms - left.epoch_ms) / 1000.0
        if not 0.5 <= seconds <= 1.5:
            raise LiveWorkloadEvidenceError(
                f"native progress cadence is not one second: {seconds}"
            )
        primary = (right.primary_total - left.primary_total) / seconds
        secondary = None
        if left.secondary_total is not None and right.secondary_total is not None:
            secondary = (right.secondary_total - left.secondary_total) / seconds
        result.append(NativeInterval(right.epoch_ms, primary, secondary))
    return tuple(result)


def parse_job_completion_events(
    jsonl_text: str, *, start_epoch_ms: int, end_epoch_ms: int,
) -> tuple[Mapping[str, Any], ...]:
    """Return successful JOB completions in the live decision interval."""
    if start_epoch_ms >= end_epoch_ms:
        raise LiveWorkloadEvidenceError("JOB interval is empty")
    rows: list[Mapping[str, Any]] = []
    for line in jsonl_text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        epoch = _integer(row.get("epoch_ms"), "JOB epoch_ms")
        latency = row.get("latency_ms")
        if isinstance(latency, bool) or not isinstance(latency, (int, float)):
            raise LiveWorkloadEvidenceError("JOB latency_ms must be numeric")
        if not math.isfinite(float(latency)) or float(latency) < 0.0:
            raise LiveWorkloadEvidenceError("JOB latency_ms must be finite and non-negative")
        if start_epoch_ms < epoch <= end_epoch_ms and row.get("status") == "complete":
            rows.append(row)
    return tuple(sorted(rows, key=lambda row: int(row["epoch_ms"])))


def latency_percentiles_ms(
    completion_rows: Sequence[Mapping[str, Any]],
) -> tuple[float, float]:
    values = sorted(float(row["latency_ms"]) for row in completion_rows)
    if not values:
        raise LiveWorkloadEvidenceError("no workload-native latency samples")

    def percentile(probability: float) -> float:
        position = (len(values) - 1) * probability
        low = math.floor(position)
        high = math.ceil(position)
        if low == high:
            return values[low]
        return values[low] * (high - position) + values[high] * (position - low)

    return percentile(0.95), percentile(0.99)

