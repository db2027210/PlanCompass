#!/usr/bin/env python3
"""Continuous multi-query JOB client with formal rolling-stop markers."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import subprocess
import threading
import time


def emit(value: dict) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--defaults-extra-file", type=Path, required=True)
    parser.add_argument("--query-dir", type=Path, required=True)
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--query-timeout-seconds", type=int, default=600)
    parser.add_argument("--maximum-measurement-seconds", type=int, default=600)
    parser.add_argument(
        "--weights",
        default="",
        help="comma-separated positive query weights in sorted SQL-file order",
    )
    parser.add_argument("--seed", type=int, default=4508333)
    args = parser.parse_args()
    if args.maximum_measurement_seconds < 180 or args.maximum_measurement_seconds % 30:
        parser.error("--maximum-measurement-seconds must be N>=180 and N%30=0")
    paths = sorted(args.query_dir.glob("*.sql"))
    if not paths:
        raise SystemExit("empty JOB SQL directory")
    queries = [(path.name, path.read_text(encoding="utf-8")) for path in paths]
    weights = (
        [float(value) for value in args.weights.split(",")]
        if args.weights
        else [1.0] * len(queries)
    )
    if len(weights) != len(queries) or any(value <= 0 for value in weights):
        raise SystemExit("weights must contain one positive value per JOB SQL file")
    # Preserve fractional formal weights exactly by scaling the smallest
    # decimal representation to integers, then shuffle one deterministic
    # finite cycle.  This gives the frozen 10/10/.../30/10/7.5 mix without
    # allowing a random sampler to drift over a short R0 window.
    scale = 10 ** max(
        len(str(value).partition(".")[2].rstrip("0")) for value in weights
    )
    counts = [int(round(value * scale)) for value in weights]
    common = counts[0]
    for value in counts[1:]:
        a, b = common, value
        while b:
            a, b = b, a % b
        common = a
    counts = [value // common for value in counts]
    weighted_schedule = [
        query
        for query, count in zip(queries, counts)
        for _ in range(count)
    ]
    random.Random(args.seed).shuffle(weighted_schedule)
    # A 600-second formal point is a bounded weighted workload sample, not a
    # promise that one serial client can finish every long-running JOB query.
    # Prefixing all queries biases short points toward file-name order and can
    # make the frozen weights unreachable.  Keep the deterministic weighted
    # cycle itself; per-query coverage remains an audit field across repeats.
    schedule = weighted_schedule
    args.event_path.parent.mkdir(parents=True, exist_ok=True)
    if args.stop_file.exists() or args.event_path.exists():
        raise SystemExit("stale JOB stop/event artifact")

    started_epoch = int(time.time() * 1000)
    measurement_epoch = started_epoch + 60_000
    emit({
        "marker": "FORMAL_SQL_SESSION_JSON",
        "measurement_start_epoch_ms": measurement_epoch,
        "warmup_seconds": 60,
        "maximum_measurement_seconds": args.maximum_measurement_seconds,
    })
    # The adapters consume the marker prefix, not the marker field.
    print("FORMAL_SQL_SESSION_JSON=" + json.dumps({
        "measurement_start_epoch_ms": measurement_epoch,
        "warmup_seconds": 60,
        "maximum_measurement_seconds": args.maximum_measurement_seconds,
    }, separators=(",", ":")), flush=True)
    deadline = time.monotonic() + 60 + args.maximum_measurement_seconds
    stop = threading.Event()
    event_lock = threading.Lock()
    completed = 0
    errors = 0

    def append_event(row: dict) -> None:
        payload = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        with event_lock:
            with args.event_path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

    def worker(worker_id: int) -> None:
        nonlocal completed, errors
        position = worker_id % len(schedule)
        while not stop.is_set() and time.monotonic() < deadline:
            name, sql = schedule[position]
            position = (position + args.concurrency) % len(schedule)
            begun = time.monotonic_ns()
            try:
                process = subprocess.run(
                    [
                        "mysql", f"--defaults-file={args.defaults_extra_file}",
                        "--protocol=tcp", f"--host={args.host}",
                        f"--port={args.port}", "--user=root", "--batch", "--raw",
                        "--skip-column-names",
                        f"--init-command=SET SESSION MAX_EXECUTION_TIME={args.query_timeout_seconds * 1000}",
                        args.database, "-e", sql,
                    ],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                    timeout=args.query_timeout_seconds + 5, check=False,
                )
                ended = time.monotonic_ns()
                if process.returncode == 0:
                    completed += 1
                    status, error = "complete", None
                else:
                    errors += 1
                    status, error = "error", (process.stderr or "")[-500:]
            except subprocess.TimeoutExpired:
                ended = time.monotonic_ns()
                errors += 1
                status, error = "error", "TimeoutExpired"
            append_event({
                "status": status, "worker": worker_id, "query": name,
                "epoch_ms": int(time.time() * 1000),
                "latency_ms": (ended - begun) / 1_000_000.0,
                "error": error,
            })

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(worker, index) for index in range(args.concurrency)]
        selected = args.maximum_measurement_seconds
        while time.monotonic() < deadline:
            if args.stop_file.is_file():
                selected = int(args.stop_file.read_text(encoding="ascii").strip())
                if (
                    selected < 180
                    or selected > args.maximum_measurement_seconds
                    or selected % 30
                ):
                    raise RuntimeError("invalid rolling stop request")
                break
            time.sleep(0.1)
        stop.set()
        for future in futures:
            future.result()
    print("FORMAL_SQL_METRICS_JSON=" + json.dumps({
        "measurement_seconds": selected,
        "completed_total": completed,
        "unexpected_errors": errors,
        "stop_requested": args.stop_file.is_file(),
    }, separators=(",", ":")), flush=True)
    return 0 if completed > 0 and errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
