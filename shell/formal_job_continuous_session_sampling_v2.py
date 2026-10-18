"""Single-process rolling measurement adapter for FormalSQLWorkload JOB runs."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence

from formal_mysql_database_qps import (
    FORMAL_DATABASE_BOUNDARY_TOLERANCE_MS,
    canonical_database_qps,
)
from formal_stability import FrozenStabilityEvaluator, StabilitySample


class FormalJobSessionError(RuntimeError):
    pass

class FormalSamplingBoundaryError(FormalJobSessionError):
    """A sampler timeout or coverage gap made this fixed boundary unusable."""



SESSION_MARKER = re.compile(r"FORMAL_SQL_SESSION_JSON=(\{[^\n]+\})")
FINAL_MARKER = re.compile(r"FORMAL_SQL_METRICS_JSON=(\{[^\n]+\})")
FORBIDDEN_SOURCES = {
    "questions", "mysql_questions", "performance_schema.questions",
    "performance_schema_global_status_questions",
}
MIN_ONE_SECOND_CADENCE_SECONDS = 0.5
MAX_ONE_SECOND_CADENCE_SECONDS = 1.5
MAX_RECOVERABLE_DELAY_SECONDS = 3.5
NOMINAL_BOUNDARY_RECOVERY_TOLERANCE_SECONDS = 0.25


def _default_process_factory(command: Sequence[str], **kwargs: Any) -> subprocess.Popen[str]:
    return subprocess.Popen(list(command), **kwargs)


class FormalJobContinuousSession:
    """Implements the campaign's structural ContinuousMeasurementSession protocol.

    ``command_builder`` must generate a FormalSQLWorkload command/config using
    the supplied stop-file path. ``resource_samples`` returns one-second cgroup
    samples containing epoch_ms and cpu/memory/io utilization ratios.
    """

    def __init__(
        self,
        *,
        output: Path,
        command_builder: Callable[[Path], Sequence[str]],
        resource_samples: Callable[[], Sequence[Mapping[str, Any]]],
        database_qps_samples: Callable[[], Sequence[Mapping[str, Any]]],
        measurement_protocol: Mapping[str, Any],
        cwd: Path | None = None,
        process_factory: Callable[..., Any] = _default_process_factory,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wait_timeout_seconds: float = 700.0,
        throughput_source: str = "formal_sql_completion_jsonl",
        required_query_names: Sequence[str] = (),
    ) -> None:
        self.output = output
        self.command_builder = command_builder
        self.resource_samples_provider = resource_samples
        if not callable(database_qps_samples):
            raise FormalJobSessionError("database_qps_samples must be callable")
        self.database_qps_samples_provider = database_qps_samples
        self.evaluator = FrozenStabilityEvaluator(measurement_protocol)
        self.cwd = cwd
        self.process_factory = process_factory
        self.monotonic = monotonic
        self.sleep = sleep
        self.wait_timeout_seconds = wait_timeout_seconds
        self.throughput_source = throughput_source.strip().lower()
        self.required_query_names = tuple(str(name) for name in required_query_names)
        if len(set(self.required_query_names)) != len(self.required_query_names):
            raise FormalJobSessionError("required JOB query names must be unique")
        self.stop_file = output / "formal_job.stop"
        self.stdout_path = output / "workload.stdout.log"
        self.stderr_path = output / "workload.stderr.log"
        self.events_path = output / "completion_events.jsonl"
        self.process: Any | None = None
        self._stdout_handle: Any | None = None
        self._stderr_handle: Any | None = None
        self._last_observation: Mapping[str, Any] | None = None
        self.maximum_measurement_seconds = 600

    def start(self, *, warmup_seconds: int, maximum_measurement_seconds: int) -> None:
        if self.process is not None:
            raise FormalJobSessionError("JOB continuous session was already started")
        if (
            warmup_seconds != 60
            or maximum_measurement_seconds < 180
            or maximum_measurement_seconds % 30
        ):
            raise FormalJobSessionError("formal JOB timing must be 60+N, N>=180 and N%30=0")
        self.maximum_measurement_seconds = maximum_measurement_seconds
        if not self.throughput_source or self.throughput_source in FORBIDDEN_SOURCES:
            raise FormalJobSessionError(
                "Questions is not workload throughput; use FormalSQLWorkload completion JSONL"
            )
        self.output.mkdir(parents=True, exist_ok=True)
        if self.stop_file.exists() or self.stop_file.is_symlink():
            raise FormalJobSessionError("stale JOB stop-file exists before session start")
        self._stdout_handle = self.stdout_path.open("w", encoding="utf-8")
        self._stderr_handle = self.stderr_path.open("w", encoding="utf-8")
        command = [str(item) for item in self.command_builder(self.stop_file)]
        if not command:
            raise FormalJobSessionError("JOB command builder returned an empty command")
        try:
            self.process = self.process_factory(
                command,
                cwd=None if self.cwd is None else str(self.cwd),
                stdout=self._stdout_handle,
                stderr=self._stderr_handle,
                text=True,
                start_new_session=True,
            )
        except Exception:
            self._close_logs()
            raise

    def _flush_logs(self) -> None:
        for handle in (self._stdout_handle, self._stderr_handle):
            if handle is not None and not handle.closed:
                handle.flush()

    def _close_logs(self) -> None:
        self._flush_logs()
        for handle in (self._stdout_handle, self._stderr_handle):
            if handle is not None and not handle.closed:
                handle.close()

    def _stdout(self) -> str:
        self._flush_logs()
        return self.stdout_path.read_text(encoding="utf-8")

    def _session_marker(self) -> Mapping[str, Any]:
        matches = SESSION_MARKER.findall(self._stdout())
        if len(matches) != 1:
            raise FormalJobSessionError(
                f"FormalSQLWorkload session marker cardinality is {len(matches)}"
            )
        marker = json.loads(matches[0])
        if (
            marker.get("warmup_seconds") != 60
            or marker.get("maximum_measurement_seconds")
            != self.maximum_measurement_seconds
        ):
            raise FormalJobSessionError("FormalSQLWorkload reported non-frozen timing")
        return marker

    def _completed_event_rows(self) -> list[Mapping[str, Any]]:
        if not self.events_path.is_file() or self.events_path.is_symlink():
            return []
        raw = self.events_path.read_text(encoding="utf-8")
        # A concurrently appended final fragment is not evidence until newline-flushed.
        complete = raw if raw.endswith("\n") else raw.rsplit("\n", 1)[0] + ("\n" if "\n" in raw else "")
        rows: list[Mapping[str, Any]] = []
        for line in complete.splitlines():
            row = json.loads(line)
            if row.get("status") != "complete":
                continue
            epoch = row.get("epoch_ms")
            latency = row.get("latency_ms")
            if (
                isinstance(epoch, bool) or not isinstance(epoch, (int, float))
                or isinstance(latency, bool) or not isinstance(latency, (int, float))
                or not math.isfinite(float(latency)) or float(latency) < 0.0
            ):
                raise FormalJobSessionError("invalid JOB completion event")
            rows.append(row)
        return sorted(rows, key=lambda row: int(row["epoch_ms"]))

    def _measurement_resources(
        self, start_epoch_ms: int, measurement_seconds: int,
    ) -> list[Mapping[str, Any]]:
        end = start_epoch_ms + measurement_seconds * 1000
        rows = [
            row for row in self.resource_samples_provider()
            if start_epoch_ms < int(row["epoch_ms"]) <= end
        ]
        rows.sort(key=lambda row: int(row["epoch_ms"]))
        if len(rows) < measurement_seconds * 0.9:
            raise FormalSamplingBoundaryError(
                f"resource sample coverage below 0.9: {len(rows)}/{measurement_seconds}"
            )
        return rows

    def wait_until_measurement(self, measurement_seconds: int) -> None:
        if measurement_seconds not in range(
            180, self.maximum_measurement_seconds + 1, 30
        ):
            raise FormalJobSessionError("decision time is outside the configured rolling schedule")
        if self.process is None:
            raise FormalJobSessionError("JOB continuous session is not started")
        marker = self._session_marker()
        end_epoch_ms = int(marker["measurement_start_epoch_ms"]) + measurement_seconds * 1000
        deadline = self.monotonic() + self.wait_timeout_seconds
        while True:
            rows = self.resource_samples_provider()
            database = self.database_qps_samples_provider()
            if (
                rows
                and database
                and max(int(row["epoch_ms"]) for row in rows) >= end_epoch_ms
                and max(int(row["epoch_ms"]) for row in database) >= end_epoch_ms
            ):
                return
            if self.process.poll() is not None:
                self._flush_logs()
                finals = FINAL_MARKER.findall(
                    self.stdout_path.read_text(encoding="utf-8")
                )
                completed_boundary = False
                if self.process.returncode == 0 and len(finals) == 1:
                    final = json.loads(finals[0])
                    completed_boundary = (
                        int(final.get("measurement_seconds", -1))
                        >= measurement_seconds
                    )
                if not completed_boundary:
                    raise FormalJobSessionError(
                        f"FormalSQLWorkload exited before {measurement_seconds}s decision: "
                        f"rc={self.process.returncode}"
                    )
                # A normal fixed-horizon client can exit milliseconds before
                # the independent resource/DB samplers publish their bracketing
                # rows.  Keep waiting for those rows until the existing
                # fail-closed boundary timeout.
            if self.monotonic() >= deadline:
                raise FormalSamplingBoundaryError(
                    f"timed out waiting for {measurement_seconds}s resource boundary"
                )
            self.sleep(0.05)

    @staticmethod
    def _percentile(values: Sequence[float], probability: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        position = (len(ordered) - 1) * probability
        low, high = math.floor(position), math.ceil(position)
        if low == high:
            return ordered[low]
        return ordered[low] * (high - position) + ordered[high] * (position - low)

    def observation(self, measurement_seconds: int) -> Mapping[str, Any]:
        if self.throughput_source in FORBIDDEN_SOURCES:
            raise FormalJobSessionError("Questions is not workload throughput")
        marker = self._session_marker()
        start = int(marker["measurement_start_epoch_ms"])
        database_throughput = canonical_database_qps(
            self.database_qps_samples_provider(), start_epoch_ms=start,
            end_epoch_ms=start + measurement_seconds * 1000,
            boundary_tolerance_ms=FORMAL_DATABASE_BOUNDARY_TOLERANCE_MS,
        )
        resources = self._measurement_resources(start, measurement_seconds)
        completions = [
            row for row in self._completed_event_rows()
            if start < int(row["epoch_ms"]) <= start + measurement_seconds * 1000
        ]
        completion_epochs = [int(row["epoch_ms"]) for row in completions]
        resource_epochs = [int(resource["epoch_ms"]) for resource in resources]
        resource_intervals = [
            (epoch - previous) / 1000.0
            for previous, epoch in zip(
                [start, *resource_epochs[:-1]], resource_epochs
            )
        ]
        usable_indexes: set[int] = set()
        rejected_cadence_intervals: list[float] = []
        recovered_catchup_pairs: list[Mapping[str, float | int]] = []
        index = 0
        while index < len(resource_intervals):
            interval = resource_intervals[index]
            if not math.isfinite(interval) or interval <= 0.0:
                raise FormalSamplingBoundaryError(
                    f"non-positive resource sample interval: {interval}"
                )
            if (
                MIN_ONE_SECOND_CADENCE_SECONDS
                <= interval
                <= MAX_ONE_SECOND_CADENCE_SECONDS
            ):
                usable_indexes.add(index)
                index += 1
                continue
            if interval > MAX_ONE_SECOND_CADENCE_SECONDS:
                following = (
                    resource_intervals[index + 1]
                    if index + 1 < len(resource_intervals)
                    else None
                )
                following_epoch = (
                    resource_epochs[index + 1]
                    if index + 1 < len(resource_epochs)
                    else None
                )
                phase_error = (
                    abs(
                        ((following_epoch - start) / 1000.0)
                        - round((following_epoch - start) / 1000.0)
                    )
                    if following_epoch is not None
                    else math.inf
                )
                recovered = (
                    interval <= MAX_RECOVERABLE_DELAY_SECONDS
                    and following is not None
                    and 0.0 < following < MIN_ONE_SECOND_CADENCE_SECONDS
                    and phase_error
                    <= NOMINAL_BOUNDARY_RECOVERY_TOLERANCE_SECONDS
                )
                if not recovered:
                    raise FormalSamplingBoundaryError(
                        "unpaired delayed resource sample or catch-up did not "
                        f"recover a nominal boundary: interval={interval}, "
                        f"following={following}, phase_error={phase_error}"
                    )
                rejected_cadence_intervals.extend((interval, following))
                recovered_catchup_pairs.append({
                    "delayed_sample_index": index + 1,
                    "catchup_sample_index": index + 2,
                    "delayed_interval_seconds": interval,
                    "catchup_interval_seconds": following,
                    "recovered_boundary_phase_error_seconds": phase_error,
                })
                index += 2
                continue
            # A short interval is never evidence for a one-second sample.
            # It is only described as a compensated catch-up when the branch
            # above proves its immediately preceding delayed pair recovered an
            # absolute nominal boundary.
            rejected_cadence_intervals.append(interval)
            index += 1

        position = 0
        previous_epoch = start
        samples: list[StabilitySample] = []
        usable_elapsed_seconds = 0.0
        for index, resource in enumerate(resources):
            epoch = resource_epochs[index]
            count = 0
            while position < len(completion_epochs) and completion_epochs[position] <= epoch:
                if completion_epochs[position] > previous_epoch:
                    count += 1
                position += 1
            interval = resource_intervals[index]
            previous_epoch = epoch
            # The sampler is scheduled on a one-second clock, but an isolated
            # host scheduling delay can produce one long interval followed by
            # a catch-up tick a few milliseconds later.  Neither row is a
            # one-second stability sample.  Reject those rows and let the
            # frozen per-window and overall >=90% coverage gates decide
            # whether enough evidence remains; do not invalidate a complete
            # 600-second run because of one auditable scheduler hiccup.
            if index not in usable_indexes:
                continue
            usable_elapsed_seconds += interval
            samples.append(StabilitySample(
                elapsed_s=(epoch - start) / 1000.0,
                cpu=float(resource["cpu_utilization_ratio"]),
                memory=float(resource["memory_utilization_ratio"]),
                io=float(resource["io_utilization_ratio"]),
                qps=count / interval,
            ))
        raw_observed_elapsed_seconds = (
            (resource_epochs[-1] - start) / 1000.0
            if resource_epochs
            else 0.0
        )
        count_coverage = len(samples) / measurement_seconds
        elapsed_coverage = usable_elapsed_seconds / measurement_seconds
        boundary_span_coverage = raw_observed_elapsed_seconds / measurement_seconds
        effective_coverage = min(
            1.0,
            count_coverage,
            elapsed_coverage,
            boundary_span_coverage,
        )
        if effective_coverage < 0.9:
            raise FormalSamplingBoundaryError(
                "one-second resource sample coverage below 0.9 after cadence "
                f"filter: effective={effective_coverage}, "
                f"count={len(samples)}/{measurement_seconds}, "
                f"usable_elapsed={usable_elapsed_seconds}/"
                f"{measurement_seconds}, raw_span="
                f"{raw_observed_elapsed_seconds}/{measurement_seconds}"
            )
        decision = self.evaluator.evaluate(
            samples, workload_is_xp=False, elapsed_s=float(measurement_seconds)
        )
        latest = [
            sample for sample in samples
            if sample.elapsed_s > measurement_seconds - 180
        ]
        latencies = [float(row["latency_ms"]) for row in completions]
        result: Mapping[str, Any] = {
            "measurement_seconds": measurement_seconds,
            "throughput_evidence": database_throughput,
            "database_throughput": database_throughput,
            "client_throughput_evidence": {
                "reliable": True,
                "source": self.throughput_source,
                "completion_event_count": len(completions),
            },
            "stability": {
                **decision.as_dict(),
                "decision_window_seconds": 180,
            },
            "resource_means": {
                "cpu": statistics.fmean(sample.cpu for sample in latest),
                "memory": statistics.fmean(sample.memory for sample in latest),
                "io": statistics.fmean(sample.io for sample in latest),
            },
            "resource_sample_coverage": effective_coverage,
            "resource_cadence_audit": {
                "raw_sample_count": len(resources),
                "usable_one_second_sample_count": len(samples),
                "rejected_sample_count": len(rejected_cadence_intervals),
                "rejected_intervals_seconds": rejected_cadence_intervals,
                "recovered_catchup_pair_count": len(
                    recovered_catchup_pairs
                ),
                "recovered_catchup_pairs": recovered_catchup_pairs,
                "minimum_interval_seconds": MIN_ONE_SECOND_CADENCE_SECONDS,
                "maximum_interval_seconds": MAX_ONE_SECOND_CADENCE_SECONDS,
                "maximum_recoverable_delay_seconds":
                    MAX_RECOVERABLE_DELAY_SECONDS,
                "nominal_boundary_recovery_tolerance_seconds":
                    NOMINAL_BOUNDARY_RECOVERY_TOLERANCE_SECONDS,
                "usable_elapsed_seconds": usable_elapsed_seconds,
                "raw_observed_elapsed_seconds":
                    raw_observed_elapsed_seconds,
                "count_coverage": min(1.0, count_coverage),
                "elapsed_coverage": min(1.0, elapsed_coverage),
                "boundary_span_coverage": min(
                    1.0, boundary_span_coverage
                ),
                "effective_coverage": effective_coverage,
            },
            "latency_p95_ms_audit": self._percentile(latencies, 0.95),
            "latency_p99_ms_audit": self._percentile(latencies, 0.99),
        }
        self._last_observation = result
        return result

    def _publish_stop(self, measurement_seconds: int) -> None:
        temporary = self.stop_file.with_name(self.stop_file.name + f".tmp.{os.getpid()}")
        if temporary.exists() or temporary.is_symlink():
            raise FormalJobSessionError(f"stale temporary stop-file exists: {temporary}")
        try:
            with temporary.open("x", encoding="ascii") as handle:
                handle.write(f"{measurement_seconds}\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.stop_file)
            directory_fd = os.open(self.stop_file.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _terminate_failed_process(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()
            self.process.wait(timeout=5)

    def stop(self, measurement_seconds: int) -> Mapping[str, Any]:
        if self.process is None:
            raise FormalJobSessionError("JOB continuous session is not started")
        if measurement_seconds not in range(180, 601, 30):
            raise FormalJobSessionError("stop time must be 180/210/.../600")
        try:
            self._publish_stop(measurement_seconds)
            returncode = self.process.wait(timeout=self.wait_timeout_seconds)
            self._close_logs()
            if returncode != 0:
                raise FormalJobSessionError(
                    f"FormalSQLWorkload failed during graceful stop: rc={returncode}; "
                    f"stderr={self.stderr_path.read_text(encoding='utf-8')[-2000:]}"
                )
            matches = FINAL_MARKER.findall(self.stdout_path.read_text(encoding="utf-8"))
            if len(matches) != 1:
                raise FormalJobSessionError(
                    f"FormalSQLWorkload final marker cardinality is {len(matches)}"
                )
            metrics = json.loads(matches[0])
            if int(metrics.get("measurement_seconds", -1)) != measurement_seconds:
                raise FormalJobSessionError("FormalSQLWorkload stopped at the wrong boundary")
            if measurement_seconds < 600 and metrics.get("stop_requested") is not True:
                raise FormalJobSessionError("FormalSQLWorkload did not acknowledge stop-file")
            start = int(self._session_marker()["measurement_start_epoch_ms"])
            database_throughput = canonical_database_qps(
                self.database_qps_samples_provider(), start_epoch_ms=start,
                end_epoch_ms=start + measurement_seconds * 1000,
                boundary_tolerance_ms=FORMAL_DATABASE_BOUNDARY_TOLERANCE_MS,
            )
            all_completed_rows = self._completed_event_rows()
            in_window_completed_rows = [
                row for row in self._completed_event_rows()
                if start < int(row["epoch_ms"]) <= start + measurement_seconds * 1000
            ]
            completed_queries = sorted({
                str(row["query"])
                for row in all_completed_rows
                if row.get("query") is not None
            })
            in_window_completed_queries = sorted({
                str(row["query"])
                for row in in_window_completed_rows
                if row.get("query") is not None
            })
            missing_queries = sorted(
                set(self.required_query_names) - set(completed_queries)
            )
            unexpected_errors = int(metrics.get("unexpected_errors", 0))
            completed_total = int(metrics.get("completed_total", len(all_completed_rows)))
            coverage_complete = not missing_queries
            correctness = {
                "passed": (
                    unexpected_errors == 0
                    and completed_total > 0
                ),
                "mode": "job_bounded_weighted_schedule_no_error",
                "required_queries": list(self.required_query_names),
                "completed_queries": completed_queries,
                "in_window_completed_queries": in_window_completed_queries,
                "graceful_drain_completed_queries": sorted(
                    set(completed_queries) - set(in_window_completed_queries)
                ),
                "missing_queries": missing_queries,
                "coverage_complete": coverage_complete,
                "coverage_role": "audit_only_per_bounded_point",
                "bounded_point_seconds": measurement_seconds,
                "unexpected_errors": unexpected_errors,
            }
            if correctness["passed"] is not True:
                raise FormalJobSessionError(
                    f"JOB correctness failed: {correctness}"
                )
            return {
                "sampling": {"warmup_seconds": 60, "measurement_seconds": measurement_seconds},
                "metrics": metrics,
                "correctness": correctness,
                "throughput_evidence": database_throughput,
                "database_throughput": database_throughput,
                "client_throughput_evidence": {
                    "reliable": True, "source": self.throughput_source,
                    "event_path": str(self.events_path),
                },
                "stdout_path": str(self.stdout_path),
                "stderr_path": str(self.stderr_path),
            }
        except Exception:
            self._terminate_failed_process()
            self._close_logs()
            raise
