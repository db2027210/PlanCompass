"""Continuous TPC-H/TPC-C adapter for the instrumented formal BenchBase jar."""

from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import re
import signal
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


class FormalBenchBaseSessionError(RuntimeError): pass


SESSION = re.compile(r"BENCHBASE_FORMAL_SESSION_JSON=(\{[^\n]+\})")
RESET = re.compile(r"BENCHBASE_FORMAL_WARMUP_RESET=(\{[^\n]+\})")
SAMPLE = re.compile(r"BENCHBASE_FORMAL_SAMPLE_JSON=(\{[^\n]+\})")
FINAL = re.compile(r"BENCHBASE_FORMAL_METRICS_JSON=(\{[^\n]+\})")
FORBIDDEN = {"questions", "mysql_questions", "performance_schema.questions", "human_throughput_log"}


def strict_elapsed_cadence_audit(
    epochs_ms: Sequence[int], *, start_ms: int, end_ms: int,
    nominal_interval_seconds: float = 1.0,
) -> Mapping[str, Any]:
    """Prove real elapsed coverage without accepting bursty catch-up samples.

    A normal interval is 0.75--1.25 times the nominal cadence.  One delayed
    interval may be paired with exactly one early interval, but both members
    and their sum have tight bounds.  This rejects count-only coverage,
    2.8s+0.01s catch-up, and alternating 0.1s/0.9s pseudo-dense samples.
    """
    if (
        nominal_interval_seconds <= 0
        or end_ms <= start_ms
        or not epochs_ms
    ):
        raise FormalBenchBaseSessionError("cadence evidence is absent")
    epochs = [int(value) for value in epochs_ms]
    if epochs != sorted(epochs) or len(epochs) != len(set(epochs)):
        raise FormalBenchBaseSessionError("cadence timestamps are not strictly ordered")
    if epochs[0] <= start_ms or epochs[-1] > end_ms:
        raise FormalBenchBaseSessionError("cadence timestamps exceed the measurement boundary")
    intervals = [
        (current - previous) / 1000.0
        for previous, current in zip([start_ms, *epochs[:-1]], epochs)
    ]
    normal_low = nominal_interval_seconds * 0.75
    normal_high = nominal_interval_seconds * 1.25
    delayed_high = nominal_interval_seconds * 1.50
    catchup_low = nominal_interval_seconds * 0.50
    catchup_high = nominal_interval_seconds * 0.75
    pair_low = nominal_interval_seconds * 1.90
    pair_high = nominal_interval_seconds * 2.10
    compensated: list[Mapping[str, Any]] = []
    index = 0
    while index < len(intervals):
        interval = intervals[index]
        if normal_low <= interval <= normal_high:
            index += 1
            continue
        if normal_high < interval <= delayed_high and index + 1 < len(intervals):
            following = intervals[index + 1]
            combined = interval + following
            if (
                catchup_low <= following <= catchup_high
                and pair_low <= combined <= pair_high
            ):
                compensated.append({
                    "delayed_sample_index": index + 1,
                    "delayed_interval_seconds": interval,
                    "catchup_interval_seconds": following,
                    "combined_interval_seconds": combined,
                })
                index += 2
                continue
        raise FormalBenchBaseSessionError(
            f"invalid strict elapsed cadence: {interval}"
        )
    measurement_seconds = (end_ms - start_ms) / 1000.0
    elapsed_coverage = (epochs[-1] - start_ms) / (end_ms - start_ms)
    count_coverage = len(epochs) / measurement_seconds
    if elapsed_coverage < 0.90 or count_coverage < 0.90:
        raise FormalBenchBaseSessionError(
            "strict elapsed sample coverage below 0.9"
        )
    return {
        "schema_version": "mysql_strict_elapsed_cadence_audit_v1",
        "passed": True,
        "nominal_interval_seconds": nominal_interval_seconds,
        "sample_count": len(epochs),
        "measurement_seconds": measurement_seconds,
        "real_elapsed_coverage": min(1.0, elapsed_coverage),
        "count_coverage": min(1.0, count_coverage),
        "minimum_interval_seconds": min(intervals),
        "maximum_interval_seconds": max(intervals),
        "compensated_pair_count": len(compensated),
        "compensated_pairs": compensated,
        "missing_sample_policy": "fail_closed",
    }


class FormalBenchBaseContinuousSession:
    def __init__(
        self, *, output: Path, benchmark: str,
        command_builder: Callable[[Path], Sequence[str]],
        resource_samples: Callable[[], Sequence[Mapping[str, Any]]],
        database_qps_samples: Callable[[], Sequence[Mapping[str, Any]]],
        measurement_protocol: Mapping[str, Any],
        tpch_correctness: Callable[[], Mapping[str, Any]] | None = None,
        tpcc_invariant: Callable[[str], Mapping[str, Any]] | None = None,
        expected_tpcc_invariant_id: str | None = None,
        cwd: Path | None = None, process_factory: Callable[..., Any] = subprocess.Popen,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep, wait_timeout_seconds: float = 700.0,
        throughput_source: str = "benchbase_formal_completed_total",
    ) -> None:
        if benchmark not in {"tpch", "tpcc"}: raise FormalBenchBaseSessionError("benchmark must be tpch or tpcc")
        self.output, self.benchmark = output, benchmark
        self.command_builder, self.resource_provider = command_builder, resource_samples
        if not callable(database_qps_samples):
            raise FormalBenchBaseSessionError("database_qps_samples must be callable")
        self.database_qps_samples_provider = database_qps_samples
        self.evaluator = FrozenStabilityEvaluator(measurement_protocol)
        self.tpch_correctness, self.tpcc_invariant = tpch_correctness, tpcc_invariant
        self.expected_tpcc_invariant_id = expected_tpcc_invariant_id
        self.cwd, self.process_factory = cwd, process_factory
        self.monotonic, self.sleep, self.wait_timeout = monotonic, sleep, wait_timeout_seconds
        self.source = throughput_source.strip().lower()
        self.stop_file = output / "formal_benchbase.stop"
        self.stdout_path, self.stderr_path = output / "workload.stdout.log", output / "workload.stderr.log"
        self.process = self.out = self.err = None
        self.pre_invariant: Mapping[str, Any] | None = None
        self.maximum_measurement_seconds = 600

    def _flush(self):
        for handle in (self.out, self.err):
            if handle is not None and not handle.closed: handle.flush()

    def _close(self):
        self._flush()
        for handle in (self.out, self.err):
            if handle is not None and not handle.closed: handle.close()

    def _text(self):
        self._flush(); return self.stdout_path.read_text(encoding="utf-8")

    def start(self, *, warmup_seconds: int, maximum_measurement_seconds: int) -> None:
        if (
            warmup_seconds != 60
            or maximum_measurement_seconds < 180
            or maximum_measurement_seconds % 30
        ):
            raise FormalBenchBaseSessionError("formal BenchBase timing must be 60+N, N>=180 and N%30=0")
        self.maximum_measurement_seconds = maximum_measurement_seconds
        if not self.source or self.source in FORBIDDEN:
            raise FormalBenchBaseSessionError("Questions/artificial Throughput lines are not workload throughput")
        if self.benchmark == "tpch" and self.tpch_correctness is None:
            raise FormalBenchBaseSessionError("NOT_READY: TPC-H Q18 correctness evidence is required")
        if self.benchmark == "tpcc":
            if self.tpcc_invariant is None:
                raise FormalBenchBaseSessionError("NOT_READY: frozen TPC-C invariant provider is required")
            self.pre_invariant = self.tpcc_invariant("pre")
            pre_id = self.pre_invariant.get("frozen_invariant_id")
            if self.pre_invariant.get("passed") is not True or not pre_id:
                raise FormalBenchBaseSessionError("NOT_READY: TPC-C pre invariant failed")
            if self.expected_tpcc_invariant_id and pre_id != self.expected_tpcc_invariant_id:
                raise FormalBenchBaseSessionError("NOT_READY: TPC-C pre invariant ID changed")
        self.output.mkdir(parents=True, exist_ok=True)
        if self.stop_file.exists() or self.stop_file.is_symlink(): raise FormalBenchBaseSessionError("stale stop-file")
        self.out, self.err = self.stdout_path.open("w", encoding="utf-8"), self.stderr_path.open("w", encoding="utf-8")
        environment = dict(os.environ)
        environment.update({"BENCHBASE_FORMAL_STOP_FILE": str(self.stop_file),
                            "BENCHBASE_FORMAL_WARMUP_SECONDS": "60",
                            "BENCHBASE_FORMAL_MAXIMUM_MEASUREMENT_SECONDS":
                                str(maximum_measurement_seconds)})
        self.process = self.process_factory(
            [str(x) for x in self.command_builder(self.stop_file)],
            cwd=None if self.cwd is None else str(self.cwd), stdout=self.out, stderr=self.err,
            text=True, start_new_session=True, env=environment,
        )

    def _marker(self):
        matches = SESSION.findall(self._text())
        if len(matches) != 1: raise FormalBenchBaseSessionError(f"session marker cardinality {len(matches)}")
        marker = json.loads(matches[0])
        resets = RESET.findall(self._text())
        if len(resets) != 1:
            raise FormalBenchBaseSessionError(f"actual warmup reset marker cardinality {len(resets)}")
        marker["measurement_start_epoch_ms"] = int(json.loads(resets[0])["epoch_ms"])
        return marker

    def wait_until_measurement(self, seconds: int) -> None:
        if seconds not in range(180, self.maximum_measurement_seconds + 1, 30):
            raise FormalBenchBaseSessionError("decision is outside the configured rolling schedule")
        start = int(self._marker()["measurement_start_epoch_ms"]); end = start + seconds * 1000
        deadline = self.monotonic() + self.wait_timeout
        while True:
            rows = [json.loads(x) for x in SAMPLE.findall(self._text())]
            resources = self.resource_provider()
            database = self.database_qps_samples_provider()
            if (rows and resources and database
                    and max(int(x["epoch_ms"]) for x in rows) >= end - 5000
                    and max(int(x["epoch_ms"]) for x in resources) >= end
                    and max(int(x["epoch_ms"]) for x in database) >= end):
                return
            if self.process.poll() is not None: raise FormalBenchBaseSessionError(f"BenchBase exited early rc={self.process.returncode}")
            if self.monotonic() >= deadline: raise FormalBenchBaseSessionError("BenchBase decision boundary timeout")
            self.sleep(.05)

    def wait_until_measurement_end(self, seconds: int) -> None:
        """Wait for the fixed measurement end without requiring query drain."""
        if seconds not in range(180, self.maximum_measurement_seconds + 1, 30):
            raise FormalBenchBaseSessionError("decision is outside the configured rolling schedule")
        marker = self._marker()
        end_epoch_ms = int(marker["measurement_start_epoch_ms"]) + seconds * 1000
        remaining = end_epoch_ms / 1000.0 - time.time()
        end_monotonic = self.monotonic() + max(0.0, remaining)
        while self.monotonic() < end_monotonic:
            if self.process.poll() is not None:
                raise FormalBenchBaseSessionError(
                    f"BenchBase exited before measurement end rc={self.process.returncode}"
                )
            self.sleep(min(.25, max(0.01, end_monotonic - self.monotonic())))

    def request_stop_and_wait(self, seconds: int, graceful_drain_seconds: float) -> int:
        """Publish the measurement stop and bound post-window query drain."""
        if graceful_drain_seconds <= 0:
            raise FormalBenchBaseSessionError("graceful drain must be positive")
        self._publish(seconds)
        try:
            return self.process.wait(timeout=graceful_drain_seconds)
        except subprocess.TimeoutExpired as error:
            raise FormalBenchBaseSessionError(
                f"BenchBase graceful drain timeout after {graceful_drain_seconds:g}s"
            ) from error

    def stop_after_measurement(
        self, seconds: int, *, graceful_drain_seconds: float = 600.0
    ) -> Mapping[str, Any]:
        """Stop at the fixed boundary, then validate a normal drained exit."""
        self.wait_until_measurement_end(seconds)
        return self.stop(seconds, graceful_drain_seconds=graceful_drain_seconds)

    def observation(self, seconds: int) -> Mapping[str, Any]:
        start = int(self._marker()["measurement_start_epoch_ms"]); end = start + seconds * 1000
        database_throughput = canonical_database_qps(
            self.database_qps_samples_provider(), start_epoch_ms=start,
            end_epoch_ms=end,
            boundary_tolerance_ms=FORMAL_DATABASE_BOUNDARY_TOLERANCE_MS,
        )
        resources = sorted((x for x in self.resource_provider() if start < int(x["epoch_ms"]) <= end), key=lambda x:int(x["epoch_ms"]))
        progress = [json.loads(x) for x in SAMPLE.findall(self._text())]
        progress = [x for x in progress if x.get("measurement_phase") is True and start <= int(x["epoch_ms"]) <= end]
        resource_cadence = strict_elapsed_cadence_audit(
            [int(x["epoch_ms"]) for x in resources],
            start_ms=start, end_ms=end,
        )
        progress_epochs = sorted(
            {
                int(x["epoch_ms"]) for x in progress
                if int(x["epoch_ms"]) > start
            }
        )
        progress_cadence = strict_elapsed_cadence_audit(
            progress_epochs, start_ms=start, end_ms=end,
        )
        # Resource and BenchBase progress samplers are independent one-second
        # clocks.  A progress tick may therefore be a few milliseconds newer
        # than the final in-window resource tick.  It cannot be used
        # causally for that resource interval; retain it in the raw log and
        # align the rate series to observations actually covered by the
        # resource timestamps.  The separate >=90% progress gate above still
        # fail-closes true coverage gaps.
        covered_progress = [
            x for x in progress if int(x["epoch_ms"]) <= int(resources[-1]["epoch_ms"])
        ]
        rates = cumulative_interval_rates(
            [int(x["epoch_ms"]) for x in resources],
            [
                (int(x["epoch_ms"]), float(x["completed_total"]))
                for x in covered_progress
            ],
            start_ms=start, end_ms=end,
        )
        samples = [StabilitySample(
            elapsed_s=(int(row["epoch_ms"])-start)/1000.0,
            cpu=float(row["cpu_utilization_ratio"]),
            memory=float(row["memory_utilization_ratio"]), io=float(row["io_utilization_ratio"]),
            qps=float(rates[i])) for i,row in enumerate(resources)]
        decision = self.evaluator.evaluate(samples, workload_is_xp=False, elapsed_s=float(seconds))
        latest = [x for x in samples if x.elapsed_s > seconds-180]
        completion_values = [int(x["completed_total"]) for x in progress]
        completion_count = (
            max(completion_values) - min(completion_values)
            if completion_values else 0
        )
        return {"measurement_seconds": seconds,
                "throughput_evidence": database_throughput,
                "database_throughput": database_throughput,
                "client_throughput_evidence": {
                    "reliable": True,
                    "source": self.source,
                    "completion_event_count": completion_count,
                },
                "stability": {**decision.as_dict(), "decision_window_seconds": 180},
                "resource_means": {key: statistics.fmean(getattr(x,key) for x in latest) for key in ("cpu","memory","io")},
                "resource_sample_coverage": resource_cadence["real_elapsed_coverage"],
                "progress_sample_coverage": progress_cadence["real_elapsed_coverage"],
                "resource_cadence_audit": resource_cadence,
                "progress_cadence_audit": progress_cadence}

    def _publish(self, seconds):
        temporary = self.stop_file.with_name(self.stop_file.name+f".tmp.{os.getpid()}")
        try:
            with temporary.open("x",encoding="ascii") as handle:
                handle.write(f"{seconds}\n"); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary,self.stop_file)
            fd=os.open(self.stop_file.parent,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if temporary.exists(): temporary.unlink()

    @staticmethod
    def _raw_metrics(path: Path, start_ms: int, end_ms: int):
        rows=[]
        with path.open(newline="",encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                raw=float(row["Start Time (microseconds)"]); start_us=raw*1_000_000 if raw<1e13 else raw
                latency_us=float(row["Latency (microseconds)"]); completed=int(start_us+latency_us)//1000
                if start_ms < completed <= end_ms: rows.append((completed,latency_us/1000.))
        values=sorted(x[1] for x in rows)
        def pct(p):
            pos=(len(values)-1)*p; lo,hi=math.floor(pos),math.ceil(pos)
            return values[lo] if lo==hi else values[lo]*(hi-pos)+values[hi]*(pos-lo)
        if not values: raise FormalBenchBaseSessionError("raw completion window is empty")
        return rows,pct(.95),pct(.99)

    def _correctness(self, final, raw_count, summary):
        errors=int(final.get("unexpected_errors",-1))
        selected=int(final.get("completed_total",-2)); late=int(final.get("post_deadline_completed_count",-4))
        reconciled=(raw_count==selected and selected+late==int(summary.get("Measured Requests",-3)))
        if self.benchmark=="tpch":
            evidence=dict(self.tpch_correctness())
            signature=evidence.get("sha256")
            passed=(evidence.get("query_id")=="Q18" and evidence.get("parameter_int")==314
                    and isinstance(evidence.get("row_count"),int) and isinstance(signature,str) and len(signature)==64
                    and evidence.get("seed_agent")==4508333 and "FORMAL_TPCH_SEED_AGENT=4508333" in self.stderr_path.read_text(encoding="utf-8")
                    and reconciled and errors==0)
            return {**evidence,"passed":passed,"raw_summary_reconciled":reconciled,"unexpected_errors":errors}
        post=dict(self.tpcc_invariant("post")); pre=dict(self.pre_invariant or {})
        expected=self.expected_tpcc_invariant_id or pre.get("frozen_invariant_id")
        passed=(pre.get("passed") is True and pre.get("frozen_invariant_id")==expected
                and post.get("frozen_invariant_id")==expected and post.get("passed") is True
                and reconciled and errors==0)
        return {"passed":passed,"pre":pre,"post":post,"raw_summary_reconciled":reconciled,"unexpected_errors":errors}

    def stop(
        self, seconds: int, *, graceful_drain_seconds: float | None = None
    ) -> Mapping[str, Any]:
        try:
            drain = self.wait_timeout if graceful_drain_seconds is None else graceful_drain_seconds
            rc = self.request_stop_and_wait(seconds, drain); self._close()
            if rc!=0: raise FormalBenchBaseSessionError(f"BenchBase graceful stop rc={rc}")
            matches=FINAL.findall(self.stdout_path.read_text(encoding="utf-8"))
            if len(matches)!=1: raise FormalBenchBaseSessionError("final marker missing")
            final=json.loads(matches[0])
            if final.get("outputs_written") is not True or int(final.get("measurement_seconds",-1))!=seconds:
                raise FormalBenchBaseSessionError("BenchBase did not finish selected outputs")
            raw=list(self.output.rglob("*.raw.csv")); summaries=list(self.output.rglob("*.summary.json"))
            if len(raw)!=1 or len(summaries)!=1: raise FormalBenchBaseSessionError("BenchBase output cardinality mismatch")
            marker=self._marker(); start=int(marker["measurement_start_epoch_ms"]); end=start+seconds*1000
            database_throughput=canonical_database_qps(
                self.database_qps_samples_provider(), start_epoch_ms=start,
                end_epoch_ms=end,
                boundary_tolerance_ms=FORMAL_DATABASE_BOUNDARY_TOLERANCE_MS,
            )
            raw_rows,p95,p99=self._raw_metrics(raw[0],start,end); summary=json.loads(summaries[0].read_text())
            correctness=self._correctness(final,len(raw_rows),summary)
            if correctness.get("passed") is not True: raise FormalBenchBaseSessionError(f"correctness failed: {correctness}")
            return {"sampling":{"warmup_seconds":60,"measurement_seconds":seconds},
                    "metrics":{**final,"qps":len(raw_rows)/seconds,"latency_p95_ms":p95,"latency_p99_ms":p99},
                    "correctness":correctness,"throughput_evidence":database_throughput,
                    "database_throughput":database_throughput,
                    "client_throughput_evidence":{"reliable":True,"source":self.source}}
        except Exception:
            if self.process is not None:
                # start() creates a new session.  TPC-C uses a Python
                # composite wrapper which starts BenchBase as its child; a
                # signal to the wrapper alone can orphan Java and contaminate
                # the next fresh replay.  Always terminate the whole owned
                # process group, even if the wrapper has just exited.
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                if self.process.poll() is None:
                    try:
                        self.process.wait(timeout=5)
                    except Exception:
                        try:
                            os.killpg(self.process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        self.process.wait(timeout=5)
            self._close();raise
