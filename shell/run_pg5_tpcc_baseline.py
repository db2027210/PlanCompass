#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from typing import Any, Dict

import psycopg2


ROOT = Path(__file__).resolve().parents[1]
ISOLATED_ROOT = Path("/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated")
for import_root in (ROOT, ISOLATED_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from system_resource_monitor import monitor_resources_from_docker_runtime_workload  # noqa: E402


METRICS_MARKER = "TPCC_SENSITIVE_METRICS_JSON="


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--container", default="pg5_tpcc_d2_20260722")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=59640)
    parser.add_argument("--database", default="fullgrid_tpcc")
    parser.add_argument(
        "--props",
        type=Path,
        default=ROOT / "config/formal/pg5_common_outer_bgallowed_20260722_v1/tpcc_baseline.props",
    )
    parser.add_argument("--warmup-seconds", type=int, default=60)
    parser.add_argument("--measurement-seconds", type=int, default=180)
    parser.add_argument("--cpu-limit-cores", type=float, default=4.0)
    parser.add_argument("--memory-limit-bytes", type=float, default=16 * 1024 ** 3)
    return parser.parse_args()


def workload_command(args: argparse.Namespace, duration: int) -> str:
    env = {
        "BENCHBASE_JAVA_XMX": "-Xmx4g",
        "BENCHBASE_RUNTIME_ROOT": str(
            args.output_dir.parent / "benchbase_runtime"
        ),
        "BENCHBASE_SCALEFACTOR": "10",
        "BENCHBASE_SOURCE_ROOT": "/home/anonymous/benchbase-postgres",
        "BENCHBASE_TERMINALS": "32",
        "BENCHBASE_TPCC_WEIGHTS": "45,43,4,4,4",
        "GENERIC_RUN_SECONDS": str(duration),
        "TPCC_SENSITIVE_CLIENTS": "1",
        "TPCC_SENSITIVE_SQL_FILE": str(
            ISOLATED_ROOT / "config/workloads/tpcc_sensitive_stocklevel.sql"
        ),
    }
    words = ["env"]
    words.extend(f"{key}={value}" for key, value in env.items())
    words.extend(
        [
            str(ISOLATED_ROOT / "shell/run_tpcc_sensitive_mix.sh"),
            "--mode",
            "generic",
            "--props",
            str(args.props.resolve()),
        ]
    )
    return shlex.join(words)


def db_calls(connection: Any) -> float:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COALESCE(sum(calls), 0)::double precision
            FROM pg_stat_statements
            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND query NOT ILIKE '%%pg_stat_statements%%'
              AND query NOT ILIKE 'SET %%'
            """
        )
        return float(cursor.fetchone()[0])


def parse_metrics(output: str) -> Dict[str, Any]:
    for line in reversed(output.splitlines()):
        index = line.find(METRICS_MARKER)
        if index >= 0:
            return dict(json.loads(line[index + len(METRICS_MARKER):].strip()))
    raise RuntimeError("TPC-C output has no combined metrics marker")


def main() -> int:
    args = parse_args()
    if args.warmup_seconds != 60 or args.measurement_seconds != 180:
        raise ValueError("R0 uses the earliest legal 60s warmup + 180s measurement")
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True, mode=0o700)
    manifest = {
        "schema_version": 1,
        "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "benchmark": "tpcc",
        "background_load_allowed": True,
        "endpoint": {
            "host": args.host,
            "port": args.port,
            "database": args.database,
        },
        "container": args.container,
        "native_terminals": 32,
        "sensitive_clients": 1,
        "transaction_weights": [45, 43, 4, 4, 4],
        "timing": {
            "warmup_seconds": 60,
            "measurement_seconds": 180,
            "hard_maximum_seconds_for_later_points": 600,
        },
    }
    (output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    warmup = subprocess.run(
        workload_command(args, args.warmup_seconds),
        shell=True,
        check=False,
        capture_output=True,
        text=True,
        timeout=args.warmup_seconds + 180,
    )
    (output_dir / "warmup.log").write_text(
        (warmup.stdout or "") + (warmup.stderr or ""),
        encoding="utf-8",
    )
    if warmup.returncode != 0:
        raise RuntimeError(f"TPC-C warmup failed: {warmup.returncode}")

    connection = psycopg2.connect(
        host=args.host,
        port=args.port,
        user="postgres",
        password="postgres",
        dbname=args.database,
    )
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_stat_statements_reset()")
    calls_before = db_calls(connection)
    started = time.monotonic()
    resource_avg, resource_stats = monitor_resources_from_docker_runtime_workload(
        workload_command(args, args.measurement_seconds),
        args.container,
        interval_s=1.0,
        warmup_s=0.0,
        memory_mode="working_set",
        max_runtime_s=args.measurement_seconds + 180,
    )
    elapsed = time.monotonic() - started
    calls_after = db_calls(connection)
    connection.close()
    workload_output = str(resource_stats.get("workload_output") or "")
    (output_dir / "measurement.log").write_text(workload_output, encoding="utf-8")
    metrics = parse_metrics(workload_output)
    unexpected_errors = len(re.findall(r"Unexpected SQL Errors", workload_output))
    serializable_stats = dict(resource_stats)
    serializable_stats.pop("workload_output", None)
    summary = {
        "schema_version": 1,
        "status": "complete",
        "stop_reason": "minimum_180_second_R0_window_completed",
        "effective_measurement_seconds": elapsed,
        "db_qps": max(0.0, calls_after - calls_before) / max(1e-9, elapsed),
        "native_tps": float(metrics.get("native_tps") or 0.0),
        "sensitive_tps": float(metrics.get("sensitive_tps") or 0.0),
        "combined_tps": float(metrics.get("combined_tps") or 0.0),
        "sensitive_latency_ms": float(metrics.get("sensitive_latency_ms") or 0.0),
        "resource_raw": resource_avg,
        "resource_utilization": {
            "cpu": float(resource_avg["cpu"]) / args.cpu_limit_cores,
            "memory": float(resource_avg["memory"]) / args.memory_limit_bytes,
            "io_bytes_per_second": float(resource_avg["io"]),
        },
        "resource_stats": serializable_stats,
        "unexpected_error_histogram_headers": unexpected_errors,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
