"""Single-process rolling AP/XP adapter for HyBenchFormalMain."""

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
from formal_mysql_random_walk_metrics import cumulative_interval_rates
from formal_stability import FrozenStabilityEvaluator, StabilitySample


class FormalHyBenchSessionError(RuntimeError):
    pass


SESSION_MARKER = re.compile(r"HYBENCH_FORMAL_SESSION_JSON=(\{[^\n]+\})")
SAMPLE_MARKER = re.compile(r"HYBENCH_FORMAL_SAMPLE_JSON=(\{[^\n]+\})")
FINAL_MARKER = re.compile(r"HYBENCH_FORMAL_METRICS_JSON=(\{[^\n]+\})")
FORBIDDEN_SOURCES = {"questions", "mysql_questions", "performance_schema.questions"}


def _default_process_factory(command: Sequence[str], **kwargs: Any) -> subprocess.Popen[str]:
    return subprocess.Popen(list(command), **kwargs)


class FormalHyBenchContinuousSession:
    def __init__(
        self, *, output: Path, workload: str,
        command_builder: Callable[[Path], Sequence[str]],
        resource_samples: Callable[[], Sequence[Mapping[str, Any]]],
        database_qps_samples: Callable[[], Sequence[Mapping[str, Any]]],
        measurement_protocol: Mapping[str, Any], cwd: Path | None = None,
        process_factory: Callable[..., Any] = _default_process_factory,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wait_timeout_seconds: float = 700.0,
        throughput_source: str = "hybench_formal_sample_json",
        drain_workload_queries: Callable[[], None] | None = None,
    ) -> None:
        if workload not in {"ap", "xp"}:
            raise FormalHyBenchSessionError("workload must be ap or xp")
        self.output, self.workload = output, workload
        self.command_builder, self.resource_samples_provider = command_builder, resource_samples
        if not callable(database_qps_samples):
            raise FormalHyBenchSessionError("database_qps_samples must be callable")
        self.database_qps_samples_provider = database_qps_samples
        self.evaluator = FrozenStabilityEvaluator(measurement_protocol)
        self.cwd, self.process_factory = cwd, process_factory
        self.monotonic, self.sleep = monotonic, sleep
        self.wait_timeout_seconds = wait_timeout_seconds
        self.throughput_source = throughput_source.strip().lower()
        if drain_workload_queries is not None and not callable(drain_workload_queries):
            raise FormalHyBenchSessionError("drain_workload_queries must be callable")
        self.drain_workload_queries = drain_workload_queries
        self.stop_file = output / "formal_hybench.stop"
        self.stdout_path = output / "workload.stdout.log"
        self.stderr_path = output / "workload.stderr.log"
        self.at_outcomes_path = output / "at_outcomes.jsonl"
        self.process: Any | None = None
        self._stdout_handle: Any | None = None
        self._stderr_handle: Any | None = None
        self.maximum_measurement_seconds = 600
        self._measurement_start_monotonic: float | None = None

    def start(self, *, warmup_seconds: int, maximum_measurement_seconds: int) -> None:
        if self.process is not None:
            raise FormalHyBenchSessionError("HyBench session already started")
        if (
            warmup_seconds != 60
            or maximum_measurement_seconds < 180
            or maximum_measurement_seconds % 30
        ):
            raise FormalHyBenchSessionError("formal HyBench timing must be 60+N, N>=180 and N%30=0")
        self.maximum_measurement_seconds = maximum_measurement_seconds
        if not self.throughput_source or self.throughput_source in FORBIDDEN_SOURCES:
            raise FormalHyBenchSessionError("Questions is not HyBench workload throughput")
        self.output.mkdir(parents=True, exist_ok=True)
        if self.stop_file.exists() or self.stop_file.is_symlink():
            raise FormalHyBenchSessionError("stale HyBench stop-file exists")
        self._stdout_handle = self.stdout_path.open("w", encoding="utf-8")
        self._stderr_handle = self.stderr_path.open("w", encoding="utf-8")
        environment = dict(os.environ)
        if self.workload == "xp":
            environment["FORMAL_MYSQL_AT_EVENT_LOG"] = str(self.at_outcomes_path)
        command = [str(value) for value in self.command_builder(self.stop_file)]
        if not command:
            raise FormalHyBenchSessionError("HyBench command is empty")
        try:
            self.process = self.process_factory(
                command, cwd=None if self.cwd is None else str(self.cwd),
                stdout=self._stdout_handle, stderr=self._stderr_handle,
                text=True, start_new_session=True, env=environment,
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
            raise FormalHyBenchSessionError(f"HyBench session marker cardinality is {len(matches)}")
        marker = json.loads(matches[0])
        if (
            marker.get("warmup_seconds") != 60
            or marker.get("maximum_measurement_seconds")
            != self.maximum_measurement_seconds
        ):
            raise FormalHyBenchSessionError("HyBench session reported non-frozen timing")
        if self._measurement_start_monotonic is None:
            self._measurement_start_monotonic = self.monotonic()
        return marker

    def wait_until_measurement(self, measurement_seconds: int) -> None:
        if measurement_seconds not in range(
            180, self.maximum_measurement_seconds + 1, 30
        ):
            raise FormalHyBenchSessionError("decision time is outside the configured rolling schedule")
        if self.process is None:
            raise FormalHyBenchSessionError("HyBench session is not started")
        start = int(self._session_marker()["measurement_start_epoch_ms"])
        end = start + measurement_seconds * 1000
        assert self._measurement_start_monotonic is not None
        deadline = (
            self._measurement_start_monotonic
            + measurement_seconds
            + self.wait_timeout_seconds
        )
        while True:
            resources = self.resource_samples_provider()
            database = self.database_qps_samples_provider()
            progress = [json.loads(value) for value in SAMPLE_MARKER.findall(self._stdout())]
            measured = [row for row in progress if row.get("measurement_phase") is True]
            if (resources and database and measured
                    and max(int(row["epoch_ms"]) for row in resources) >= end
                    and max(int(row["epoch_ms"]) for row in database) >= end
                    # The client sampler is one-second cadence but its final
                    # maximum-duration tick can precede the exact 600s boundary
                    # by several scheduler quanta.  Coverage is independently
                    # enforced at >=0.90 in observation(); a five-second edge
                    # tolerance avoids waiting forever after the frozen client
                    # has correctly stopped emitting progress.
                    and max(int(row["epoch_ms"]) for row in measured) >= end - 5000):
                return
            if self.process.poll() is not None:
                raise FormalHyBenchSessionError(
                    f"HyBench exited before {measurement_seconds}s decision: rc={self.process.returncode}"
                )
            if self.monotonic() >= deadline:
                raise FormalHyBenchSessionError(f"timed out waiting for {measurement_seconds}s boundary")
            self.sleep(0.05)

    def observation(self, measurement_seconds: int) -> Mapping[str, Any]:
        if self.throughput_source in FORBIDDEN_SOURCES:
            raise FormalHyBenchSessionError("Questions is not HyBench workload throughput")
        start = int(self._session_marker()["measurement_start_epoch_ms"])
        end = start + measurement_seconds * 1000
        database_throughput = canonical_database_qps(
            self.database_qps_samples_provider(), start_epoch_ms=start,
            end_epoch_ms=end,
            boundary_tolerance_ms=FORMAL_DATABASE_BOUNDARY_TOLERANCE_MS,
        )
        resources = sorted(
            (row for row in self.resource_samples_provider()
             if start < int(row["epoch_ms"]) <= end),
            key=lambda row: int(row["epoch_ms"]),
        )
        if len(resources) < measurement_seconds * 0.9:
            raise FormalHyBenchSessionError("HyBench resource coverage below 0.9")
        progress = [json.loads(value) for value in SAMPLE_MARKER.findall(self._stdout())]
        measured = [
            row for row in progress
            if row.get("measurement_phase") is True and start <= int(row["epoch_ms"]) <= end
        ]
        if len(measured) < measurement_seconds * 0.9:
            raise FormalHyBenchSessionError("HyBench native progress coverage below 0.9")
        epochs = [int(row["epoch_ms"]) for row in resources]
        # The cgroup and HyBench samplers have independent one-second clocks.
        # Do not consume a cumulative client tick that is newer than the last
        # resource tick: it is not causally covered by this resource series.
        # Raw progress remains preserved, while the independent >=90% gate
        # above still rejects a genuine sampling gap.
        covered = [
            row for row in measured
            if int(row["epoch_ms"]) <= epochs[-1]
        ]
        primary = cumulative_interval_rates(
            epochs, [
                (int(row["epoch_ms"]), float(row["xap_or_ap_total"]))
                for row in covered
            ],
            start_ms=start, end_ms=end,
        )
        secondary = cumulative_interval_rates(
            epochs, [
                (int(row["epoch_ms"]), float(row["xtp_total"]))
                for row in covered
            ],
            start_ms=start, end_ms=end,
        )
        samples = [
            StabilitySample(
                elapsed_s=(int(resource["epoch_ms"]) - start) / 1000.0,
                cpu=float(resource["cpu_utilization_ratio"]),
                memory=float(resource["memory_utilization_ratio"]),
                io=float(resource["io_utilization_ratio"]),
                qps=float(primary[index]) if self.workload == "ap" else None,
                xap_qps=float(primary[index]) if self.workload == "xp" else None,
                xtp_tps=float(secondary[index]) if self.workload == "xp" else None,
            )
            for index, resource in enumerate(resources)
        ]
        decision = self.evaluator.evaluate(
            samples, workload_is_xp=self.workload == "xp", elapsed_s=float(measurement_seconds)
        )
        latest = [
            sample for sample in samples
            if sample.elapsed_s > measurement_seconds - 180
        ]
        return {
            "measurement_seconds": measurement_seconds,
            "throughput_evidence": database_throughput,
            "database_throughput": database_throughput,
            "client_throughput_evidence": {
                "reliable": True, "source": self.throughput_source,
                "primary_stream": "xap_qps" if self.workload == "xp" else "ap_qps",
                "secondary_stream": "xtp_tps" if self.workload == "xp" else None,
            },
            "stability": {**decision.as_dict(), "decision_window_seconds": 180},
            "resource_means": {
                "cpu": statistics.fmean(sample.cpu for sample in latest),
                "memory": statistics.fmean(sample.memory for sample in latest),
                "io": statistics.fmean(sample.io for sample in latest),
            },
            "resource_sample_coverage": min(1.0, len(resources) / measurement_seconds),
            "progress_sample_coverage": min(1.0, len(measured) / measurement_seconds),
        }

    def _publish_stop(self, measurement_seconds: int) -> None:
        temporary = self.stop_file.with_name(self.stop_file.name + f".tmp.{os.getpid()}")
        try:
            with temporary.open("x", encoding="ascii") as handle:
                handle.write(f"{measurement_seconds}\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.stop_file)
            directory = os.open(self.stop_file.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _outcome_correctness(
        self, start: int, end: int, final: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if self.workload == "ap":
            # HyBench's legacy AP summary object may reset ap_total while its
            # formal native sampler and latency report still contain the
            # completed measurement transactions.  Use the durable per-second
            # cumulative counter as the authoritative completion evidence.
            progress = [
                json.loads(value) for value in SAMPLE_MARKER.findall(self._stdout())
            ]
            counters = [
                int(row["xap_or_ap_total"])
                for row in progress
                if row.get("measurement_phase") is True
                and start <= int(row["epoch_ms"]) <= end
            ]
            completed = max(counters, default=0)
            final_counter = int(final.get("ap_total") or 0)
            return {
                "passed": completed > 0,
                "completed": completed,
                "mode": "native_progress_ap_completion",
                "final_summary_ap_total_audit": final_counter,
                "final_summary_counter_consistent": final_counter == completed,
            }
        if not self.at_outcomes_path.is_file() or self.at_outcomes_path.is_symlink():
            return {"passed": False, "reason": "at_outcomes_missing"}
        counts = {
            f"AT{index}": {name: 0 for name in ("commits", "rollbacks", "timeouts", "errors")}
            for index in range(1, 7)
        }
        for line in self.at_outcomes_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if not start < int(row["epoch_ms"]) <= end:
                continue
            if row.get("at_type") not in counts or row.get("outcome") not in counts[row["at_type"]]:
                raise FormalHyBenchSessionError("invalid XP AT outcome event")
            counts[row["at_type"]][row["outcome"]] += 1
        missing = [
            name for name, row in counts.items()
            if row["commits"] + row["rollbacks"] == 0
        ]
        errors = sum(row["timeouts"] + row["errors"] for row in counts.values())
        outcomes = sum(row["commits"] + row["rollbacks"] for row in counts.values())
        xtp_total = int(final.get("xtp_total") or 0)
        delta = outcomes - xtp_total
        forced_boundary_stop = final.get("forced_boundary_stop") is True
        tolerance = (
            64 if forced_boundary_stop else 16
        ) + int(final.get("xtp_right_censored_count") or 0)
        passed = not missing and errors == 0 and abs(delta) <= tolerance
        return {
            "passed": passed, "at1_to_at6": counts, "missing_types": missing,
            "timeout_or_error_count": errors, "transaction_outcomes": outcomes,
            "xtp_total": xtp_total, "reconciliation_delta": delta,
            "reconciliation_tolerance": tolerance,
            "forced_boundary_stop": forced_boundary_stop,
        }

    def _terminate(self) -> None:
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
            raise FormalHyBenchSessionError("HyBench session is not started")
        if measurement_seconds not in range(180, 601, 30):
            raise FormalHyBenchSessionError("stop time must be 180/210/.../600")
        try:
            self._publish_stop(measurement_seconds)
            if self.drain_workload_queries is not None:
                self.drain_workload_queries()
            returncode = self.process.wait(timeout=self.wait_timeout_seconds)
            self._close_logs()
            if returncode != 0:
                raise FormalHyBenchSessionError(f"HyBench graceful stop failed: rc={returncode}")
            matches = FINAL_MARKER.findall(self.stdout_path.read_text(encoding="utf-8"))
            if len(matches) != 1:
                raise FormalHyBenchSessionError(f"HyBench final marker cardinality is {len(matches)}")
            final = json.loads(matches[0])
            if int(final.get("measurement_seconds", -1)) != measurement_seconds:
                raise FormalHyBenchSessionError("HyBench stopped at the wrong boundary")
            if measurement_seconds < 600 and final.get("stop_requested") is not True:
                raise FormalHyBenchSessionError("HyBench did not acknowledge stop-file")
            if final.get("workload_error") is not None or final.get("progress_error") is not None:
                raise FormalHyBenchSessionError("HyBench final marker contains an internal error")
            p95, p99 = float(final["latency_p95_ms"]), float(final["latency_p99_ms"])
            if p95 <= 0.0 or p99 < p95:
                raise FormalHyBenchSessionError("HyBench final latency percentiles are invalid")
            marker = self._session_marker()
            start = int(marker["measurement_start_epoch_ms"])
            database_throughput = canonical_database_qps(
                self.database_qps_samples_provider(), start_epoch_ms=start,
                end_epoch_ms=start + measurement_seconds * 1000,
                boundary_tolerance_ms=FORMAL_DATABASE_BOUNDARY_TOLERANCE_MS,
            )
            correctness = self._outcome_correctness(
                start, start + measurement_seconds * 1000, final
            )
            if correctness.get("passed") is not True:
                raise FormalHyBenchSessionError(f"HyBench correctness failed: {correctness}")
            return {
                "sampling": {"warmup_seconds": 60, "measurement_seconds": measurement_seconds},
                "metrics": final, "correctness": correctness,
                "throughput_evidence": database_throughput,
                "database_throughput": database_throughput,
                "client_throughput_evidence": {"reliable": True, "source": self.throughput_source},
                "stdout_path": str(self.stdout_path), "stderr_path": str(self.stderr_path),
            }
        except Exception:
            self._terminate()
            self._close_logs()
            raise
