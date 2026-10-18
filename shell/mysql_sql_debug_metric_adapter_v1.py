#!/usr/bin/env python3
"""Run sealed SQL files and emit fresh debug-qualification workload metrics.

This is a workload-native adapter: ``completed_total`` is incremented only
after the MySQL client returns successfully, and latency is measured around
that exact client invocation.  While every query is still in flight, the
adapter emits the real elapsed duration of the oldest in-flight query instead
of inventing percentile values.  It is intentionally debug-only and creates
no capture, R0, formal, or accepted-round artifact.

The implementation stays compatible with Python 3.6 for the JOB node02
runtime.
"""

from __future__ import print_function

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import threading
import time


SAMPLE_SCHEMA = "mysql_algorithm_c_debug_workload_metric_sample_v1"


class AdapterError(RuntimeError):
    pass


def _percentile(values, probability):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise AdapterError("latency percentile input is empty")
    position = (len(ordered) - 1) * float(probability)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    return (
        ordered[lower] * (upper - position)
        + ordered[upper] * (position - lower)
    )


def _weighted_schedule(paths, weights, seed):
    if not paths:
        raise AdapterError("sealed SQL query directory is empty")
    if len(paths) != len(weights) or any(value <= 0 for value in weights):
        raise AdapterError("one positive weight is required per SQL file")
    scale = 10 ** max(
        len(str(value).partition(".")[2].rstrip("0")) for value in weights
    )
    counts = [int(round(value * scale)) for value in weights]
    common = counts[0]
    for value in counts[1:]:
        left, right = common, value
        while right:
            left, right = right, left % right
        common = left
    schedule = [
        path
        for path, count in zip(paths, [value // common for value in counts])
        for _ in range(count)
    ]
    random.Random(seed).shuffle(schedule)
    return schedule


class NativeMetrics(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.completed_total = 0
        self.latencies_ms = []
        self.in_flight = {}
        self.failure = None

    def begin(self, worker_id, begun):
        with self.lock:
            self.in_flight[worker_id] = begun

    def complete(self, worker_id, latency_ms):
        with self.lock:
            self.in_flight.pop(worker_id, None)
            self.completed_total += 1
            self.latencies_ms.append(float(latency_ms))

    def fail(self, worker_id, message):
        with self.lock:
            self.in_flight.pop(worker_id, None)
            if self.failure is None:
                self.failure = str(message)

    def cancel(self, worker_id):
        """Forget an in-flight query stopped only by the campaign deadline."""
        with self.lock:
            self.in_flight.pop(worker_id, None)

    def document(self, attempt_id, sequence, now_epoch, now_monotonic):
        with self.lock:
            result = {
                "schema_version": SAMPLE_SCHEMA,
                "qualification_attempt_id": attempt_id,
                "sequence": int(sequence),
                "epoch_seconds": float(now_epoch),
                "completed_total": int(self.completed_total),
            }
            # The shared collector's native SQL contract is intentionally a
            # single query-duration stream.  Do not switch the schema to
            # transactional p50/p95/p99 after the first completion: that
            # makes an otherwise healthy 180-second stream fail closed at a
            # data-dependent instant.  Prefer the longest currently running
            # query; if the workers are between queries, retain the most
            # recently completed native duration.
            if self.in_flight:
                result["query_duration_ms"] = max(
                    0.0,
                    max(
                        now_monotonic - begun
                        for begun in self.in_flight.values()
                    )
                    * 1000.0,
                )
            elif self.latencies_ms:
                result["query_duration_ms"] = float(self.latencies_ms[-1])
            else:
                raise AdapterError(
                    "no completed or in-flight native SQL latency exists"
                )
            return result


def _append_sample(path, document):
    payload = (
        json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    with path.open("a", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _validate_path(path, label, require_existing_file=False):
    if not path.is_absolute() or path.is_symlink():
        raise AdapterError("%s path is not absolute and regular" % label)
    if require_existing_file and not path.is_file():
        raise AdapterError("%s is absent" % label)


def _deadline_bounded_query_timeouts(deadline, requested_seconds, now=None):
    """Return MySQL/client timeouts that cannot outlive the run deadline.

    MySQL's session timeout is the primary bound.  The subprocess timeout has
    a two-second transport/reaping allowance, which is still well inside the
    collector's 75-second graceful-exit allowance.
    """
    if now is None:
        now = time.monotonic()
    remaining = float(deadline) - float(now)
    if remaining <= 0.0:
        return None
    bounded = min(float(requested_seconds), remaining)
    mysql_timeout_ms = max(1, int(math.ceil(bounded * 1000.0)))
    client_timeout_seconds = max(0.1, bounded + 2.0)
    return mysql_timeout_ms, client_timeout_seconds


def run_adapter(args):
    attempt = str(args.attempt_id)
    if len(attempt) < 32:
        raise AdapterError("qualification attempt ID is absent or short")
    _validate_path(args.mysql_binary, "MySQL client", True)
    _validate_path(args.defaults_extra_file, "MySQL defaults file", True)
    _validate_path(args.query_dir, "SQL query directory")
    if not args.query_dir.is_dir():
        raise AdapterError("SQL query directory is absent")
    _validate_path(args.metric_stream, "metric stream")
    if not args.metric_stream.parent.is_dir():
        raise AdapterError("metric stream parent is absent")
    if args.metric_stream.exists():
        raise AdapterError("stale metric stream exists")
    if args.duration_seconds < 181 or args.duration_seconds > 600:
        raise AdapterError("debug adapter duration is outside 181..600s")
    if args.concurrency < 1 or args.concurrency > 64:
        raise AdapterError("debug adapter concurrency is outside 1..64")
    if args.query_timeout_seconds < 1 or args.query_timeout_seconds > 600:
        raise AdapterError("debug query timeout is outside 1..600s")

    paths = sorted(args.query_dir.glob("*.sql"))
    weights = (
        [float(value) for value in args.weights.split(",")]
        if args.weights
        else [1.0] * len(paths)
    )
    schedule = _weighted_schedule(paths, weights, args.seed)
    statements = {
        path: path.read_text(encoding="utf-8") for path in schedule
    }
    if any(not statement.strip() for statement in statements.values()):
        raise AdapterError("empty sealed SQL statement")

    metrics = NativeMetrics()
    stop = threading.Event()
    q15_lock = threading.Lock()

    def worker(worker_id):
        position = worker_id % len(schedule)
        while not stop.is_set() and time.monotonic() < deadline:
            path = schedule[position]
            position = (position + args.concurrency) % len(schedule)
            begun = time.monotonic()
            timeout_bounds = _deadline_bounded_query_timeouts(
                deadline, args.query_timeout_seconds, begun
            )
            if timeout_bounds is None:
                return
            mysql_timeout_ms, client_timeout_seconds = timeout_bounds
            metrics.begin(worker_id, begun)
            try:
                command = [
                    str(args.mysql_binary),
                    "--defaults-extra-file=%s" % args.defaults_extra_file,
                    "--protocol=TCP",
                    "--host=%s" % args.host,
                    "--port=%d" % args.port,
                    "--user=%s" % args.user,
                    "--batch",
                    "--raw",
                    "--skip-column-names",
                    "--init-command=SET SESSION MAX_EXECUTION_TIME=%d"
                    % mysql_timeout_ms,
                    args.database,
                    "--execute",
                    statements[path],
                ]
                # Production TPC-H Q15 uses the sealed shared ``revenue0``
                # view.  Multiple client terminals must not race its
                # CREATE/SELECT/DROP transaction.
                lock = q15_lock if path.name == "Q15.sql" else None
                if lock is not None:
                    lock.acquire()
                try:
                    completed = subprocess.run(
                        command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        timeout=client_timeout_seconds,
                        check=False,
                    )
                finally:
                    if lock is not None:
                        lock.release()
            except subprocess.TimeoutExpired as exc:
                if stop.is_set() or time.monotonic() >= deadline:
                    metrics.cancel(worker_id)
                    return
                metrics.fail(worker_id, "%s: %s" % (path.name, exc))
                stop.set()
                return
            except OSError as exc:
                metrics.fail(worker_id, "%s: %s" % (path.name, exc))
                stop.set()
                return
            latency_ms = (time.monotonic() - begun) * 1000.0
            if completed.returncode != 0:
                if stop.is_set() or time.monotonic() >= deadline:
                    metrics.cancel(worker_id)
                    return
                metrics.fail(
                    worker_id,
                    "%s rc=%d stderr=%s"
                    % (
                        path.name,
                        completed.returncode,
                        (completed.stderr or "")[-500:],
                    ),
                )
                stop.set()
                return
            metrics.complete(worker_id, latency_ms)

    started = time.monotonic()
    deadline = started + args.duration_seconds
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(worker, worker_id)
            for worker_id in range(args.concurrency)
        ]
        ready_deadline = time.monotonic() + 0.75
        while True:
            with metrics.lock:
                ready = bool(metrics.in_flight or metrics.latencies_ms)
                failure = metrics.failure
            if failure is not None:
                raise AdapterError(failure)
            if ready:
                break
            if time.monotonic() >= ready_deadline:
                raise AdapterError("SQL workers did not expose native latency")
            time.sleep(0.005)

        sequence = 0
        next_sample = time.monotonic()
        while time.monotonic() < deadline and not stop.is_set():
            now_monotonic = time.monotonic()
            if now_monotonic < next_sample:
                time.sleep(min(0.02, next_sample - now_monotonic))
                continue
            with metrics.lock:
                failure = metrics.failure
            if failure is not None:
                raise AdapterError(failure)
            document = metrics.document(
                attempt, sequence, time.time(), now_monotonic
            )
            _append_sample(args.metric_stream, document)
            print(
                "DEBUG_SQL_METRIC_SAMPLE_JSON="
                + json.dumps(document, sort_keys=True, separators=(",", ":")),
                flush=True,
            )
            sequence += 1
            next_sample = started + sequence
        stop.set()
        for future in futures:
            future.result()
    if metrics.failure is not None:
        raise AdapterError(metrics.failure)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--metric-stream", type=Path, required=True)
    parser.add_argument("--mysql-binary", type=Path, required=True)
    parser.add_argument("--defaults-extra-file", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--database", required=True)
    parser.add_argument("--query-dir", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--weights", default="")
    parser.add_argument("--seed", type=int, default=4508333)
    parser.add_argument("--query-timeout-seconds", type=int, default=600)
    parser.add_argument("--duration-seconds", type=int, default=240)
    args = parser.parse_args()
    try:
        return run_adapter(args)
    except (AdapterError, OSError, ValueError) as exc:
        print("DEBUG_SQL_METRIC_ADAPTER_FAILED: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
