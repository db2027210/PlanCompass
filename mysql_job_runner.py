"""Run real JOB queries against MySQL and emit optimizer measurement JSON."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List

from mysql_resource_monitor import run_and_monitor


QUERY_NAME = re.compile(r"^[0-9]+[a-z]\.sql$")


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _mysql_command(args: argparse.Namespace, query: str) -> List[str]:
    return [
        "mysql", "--protocol=tcp", f"--host={args.host}", f"--port={args.port}",
        f"--user={args.user}", f"--password={args.password}", "--batch", "--raw",
        "--skip-column-names", f"--init-command=SET SESSION MAX_EXECUTION_TIME={args.query_timeout_ms}",
        args.database, "-e", query,
    ]


def workload(args: argparse.Namespace) -> Dict[str, Any]:
    paths = [path for path in args.query_dir.iterdir() if path.is_file() and QUERY_NAME.match(path.name)]
    paths.sort(key=lambda path: (int(re.match(r"[0-9]+", path.name).group()), path.name))
    if args.query_names:
        wanted = set(args.query_names.split(","))
        paths = [path for path in paths if path.name in wanted]
    if not paths:
        raise RuntimeError(f"no JOB queries selected from {args.query_dir}")
    queries = [(path.name, path.read_text(encoding="utf-8")) for path in paths]
    deadline = time.monotonic() + args.duration_sec
    lock = threading.Lock()
    latencies: List[float] = []
    failures: Dict[str, int] = {}
    completed_by_query: Dict[str, int] = {}

    def worker(worker_id: int) -> None:
        position = worker_id % len(queries)
        while time.monotonic() < deadline:
            name, query = queries[position]
            position = (position + args.concurrency) % len(queries)
            started = time.monotonic()
            try:
                proc = subprocess.run(
                    _mysql_command(args, query), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    text=True, timeout=max(2.0, args.query_timeout_ms / 1000.0 + 3.0), check=False,
                )
                elapsed_ms = (time.monotonic() - started) * 1000.0
                with lock:
                    if proc.returncode == 0:
                        latencies.append(elapsed_ms)
                        completed_by_query[name] = completed_by_query.get(name, 0) + 1
                    else:
                        failures[name] = failures.get(name, 0) + 1
            except subprocess.TimeoutExpired:
                with lock:
                    failures[name] = failures.get(name, 0) + 1

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(worker, range(args.concurrency)))
    elapsed = max(time.monotonic() - started, 1e-9)
    return {
        "qps": len(latencies) / elapsed,
        "latency_p95_ms": _percentile(latencies, 95.0),
        "latency_p99_ms": _percentile(latencies, 99.0),
        "query_count": len(latencies),
        "query_time": elapsed,
        "failures": failures,
        "completed_by_query": completed_by_query,
    }


def _explain(args: argparse.Namespace) -> str:
    paths = sorted(path for path in args.query_dir.iterdir() if path.is_file() and QUERY_NAME.match(path.name))
    if args.query_names:
        wanted = set(args.query_names.split(","))
        paths = [path for path in paths if path.name in wanted]
    if not paths:
        return "EXPLAIN unavailable: no JOB query"
    query = paths[0].read_text(encoding="utf-8").strip().rstrip(";")
    proc = subprocess.run(_mysql_command(args, "EXPLAIN FORMAT=JSON " + query), capture_output=True, text=True, timeout=30, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else f"EXPLAIN unavailable: {(proc.stderr or proc.stdout)[-1000:].strip()}"


def measure(args: argparse.Namespace) -> Dict[str, Any]:
    base = [
        sys.executable, str(Path(__file__).resolve()), "--worker", "--host", args.host,
        "--port", str(args.port), "--user", args.user, "--password", args.password,
        "--database", args.database, "--query-dir", str(args.query_dir),
        "--concurrency", str(args.concurrency), "--query-timeout-ms", str(args.query_timeout_ms),
    ]
    if args.query_names:
        base.extend(["--query-names", args.query_names])
    if args.prewarm_sql:
        prewarm = subprocess.run(
            _mysql_command(args, "SET SESSION MAX_EXECUTION_TIME=0; " + args.prewarm_sql),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            timeout=float(args.prewarm_timeout_sec), check=False,
        )
        if prewarm.returncode != 0:
            detail = (prewarm.stderr or "JOB prewarm failed").strip()
            raise RuntimeError(f"JOB prewarm failed: {detail[-2000:]}")
    if args.warmup_duration_sec > 0:
        subprocess.run(base + ["--duration-sec", str(args.warmup_duration_sec)], check=True, stdout=subprocess.DEVNULL)
    command = base + ["--duration-sec", str(args.duration_sec)]
    resource = run_and_monitor(command, pids=[args.mysqld_pid], interval_sec=args.interval_sec, warmup_samples=args.warmup_samples)
    resource_keys = {"cpu", "memory", "io", "stability", "measurement_elapsed_sec"}
    detail = {key: resource.pop(key) for key in list(resource) if key not in resource_keys}
    args.output_root.mkdir(parents=True, exist_ok=True)
    artifact = args.output_root / f"job_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns()}.json"
    artifact.write_text(json.dumps(detail, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    qps = float(detail.get("qps", 0.0))
    p95 = float(detail.get("latency_p95_ms", 0.0))
    p99 = float(detail.get("latency_p99_ms", 0.0))
    return {
        "resource": resource, "qps": qps,
        "query_count": float(detail.get("query_count", 0.0)), "query_time": float(detail.get("query_time", 0.0)),
        "_stats": {"qps": {"avg": qps}, "query_count": {"avg": float(detail.get("query_count", 0.0))},
                   "query_time": {"avg": float(detail.get("query_time", 0.0))},
                   "benchmark": {"latency_p95": p95, "latency_p99": p99, "throughput": qps, "benchmark": "job"}},
        "_metadata": {"db_qps": qps, "latency_p95_ms": p95, "latency_p99_ms": p99,
                      "summary_path": str(artifact.resolve()), "explain_samples": [_explain(args)],
                      "prewarm_sql": args.prewarm_sql or None},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default='CHANGE_ME')
    parser.add_argument("--database", default="job")
    parser.add_argument("--query-dir", type=Path, default=Path("/home/anonymous/dataset/pg_data/join-order-benchmark"))
    parser.add_argument("--query-names", default="")
    parser.add_argument("--prewarm-sql", default="")
    parser.add_argument("--prewarm-timeout-sec", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--duration-sec", type=int, default=30)
    parser.add_argument("--warmup-duration-sec", type=int, default=0)
    parser.add_argument("--query-timeout-ms", type=int, default=15000)
    parser.add_argument("--mysqld-pid", type=int)
    parser.add_argument("--interval-sec", type=float, default=1.0)
    parser.add_argument("--warmup-samples", type=int, default=1)
    parser.add_argument("--output-root", type=Path, default=Path("history/mysql/job_measurements"))
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(workload(args), sort_keys=True), flush=True)
        return 0
    if not args.mysqld_pid:
        parser.error("--mysqld-pid is required outside --worker mode")
    print("RESOURCE_METRICS=" + json.dumps(measure(args), ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
