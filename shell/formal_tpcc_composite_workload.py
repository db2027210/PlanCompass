#!/usr/bin/env python3
"""Run formal BenchBase TPC-C plus the frozen global sensitive query.

The sensitive query is one part of the frozen TPC-C workload, not a monitor.
It executes once every 15 seconds while the single BenchBase process is alive
and records every completion/error as durable JSONL evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


REQUIRED_CONSUMERS = (
    "events_statements_history_long",
    "events_waits_history_long",
)
TYPED_TRACE_SCHEMA = "mysql_tpcc_jdbc_typed_trace_v1"
SENSITIVE_SQL_SHA256 = (
    "cd8fed7df71c5a681b001083397181804b185816225e68ae8449f8cc384d507e"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite TPC-C authority: {path}")
    payload = (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def enable_typed_consumers_before_workload(
    args: argparse.Namespace, trace_path: Path
) -> Path:
    """Enable and read back the exact typed-metric consumers before launch."""
    configured_defaults_input = Path(
        os.environ["MYSQL_TPCC_TYPED_DEFAULTS_FILE"]
    )
    if (
        args.defaults_extra_file.is_symlink()
        or configured_defaults_input.is_symlink()
    ):
        raise RuntimeError("TPC-C typed defaults file symlink is prohibited")
    defaults = args.defaults_extra_file.resolve(strict=True)
    configured_defaults = configured_defaults_input.resolve(strict=True)
    if (
        defaults != configured_defaults
        or defaults.is_symlink()
        or not defaults.is_file()
        or trace_path.parent != args.event_path.resolve().parent
    ):
        raise RuntimeError("TPC-C typed prelaunch authority path differs")
    sql = (
        "UPDATE performance_schema.setup_consumers SET ENABLED='YES' "
        "WHERE NAME IN "
        "('events_statements_history_long','events_waits_history_long');"
        "SELECT NAME,ENABLED FROM performance_schema.setup_consumers "
        "WHERE NAME IN "
        "('events_statements_history_long','events_waits_history_long') "
        "ORDER BY NAME;"
        "SELECT @@performance_schema_events_statements_history_long_size,"
        "@@performance_schema_events_waits_history_long_size"
    )
    command = [
        "mysql",
        f"--defaults-file={defaults}",
        "--protocol=tcp",
        f"--host={args.host}",
        f"--port={args.port}",
        "--user=root",
        "--connect-timeout=30",
        "--ssl-mode=DISABLED",
        "--get-server-public-key",
        "--batch",
        "--raw",
        "--skip-column-names",
        "-e",
        sql,
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    lines = [line.split("\t") for line in completed.stdout.splitlines()]
    consumers = {
        row[0]: row[1]
        for row in lines
        if len(row) == 2 and row[0] in REQUIRED_CONSUMERS
    }
    size_rows = [
        row
        for row in lines
        if len(row) == 2 and all(item.isdigit() for item in row)
    ]
    if (
        completed.returncode != 0
        or consumers
        != {
            "events_statements_history_long": "YES",
            "events_waits_history_long": "YES",
        }
        or len(size_rows) != 1
        or any(int(value) <= 0 for value in size_rows[0])
    ):
        raise RuntimeError(
            "TPC-C typed Performance Schema prelaunch enable/readback failed: "
            + completed.stderr[-1000:]
        )
    authority = trace_path.with_name(
        trace_path.name + ".performance_schema_authority.json"
    )
    atomic_json(
        authority,
        {
            "schema_version":
                "mysql_tpcc_typed_performance_schema_prelaunch_authority_v1",
            "status": "PASS_ENABLED_BEFORE_WORKLOAD_START",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "capture_only": True,
            "trace_path": str(trace_path),
            "consumers": consumers,
            "history_long_sizes": {
                "events_statements_history_long": int(size_rows[0][0]),
                "events_waits_history_long": int(size_rows[0][1]),
            },
            "defaults_file_path_verified_without_credential_capture":
                str(defaults),
            "qps_resource_input_prohibited": True,
            "outer_execution_authorized": False,
            "accepted_round_authorized": False,
        },
    )
    return authority


def wait_for_real_typed_join_authority(
    trace_path: Path, sensitive_sql: Path, consumer_authority: Path
) -> Path:
    """Bind JOINER to the OWNER-created real trace header and lock."""
    lock_path = trace_path.with_name(trace_path.name + ".lock")
    deadline = time.monotonic() + 120.0
    header = None
    while time.monotonic() < deadline:
        if (
            not trace_path.is_symlink()
            and trace_path.is_file()
            and not lock_path.is_symlink()
            and lock_path.is_file()
        ):
            try:
                with trace_path.open("r", encoding="utf-8") as stream:
                    header = json.loads(stream.readline())
            except (OSError, UnicodeError, json.JSONDecodeError):
                header = None
            if isinstance(header, dict):
                break
        time.sleep(0.05)
    expected_manifest = os.environ["MYSQL_TPCC_TYPED_MANIFEST_SHA256"]
    expected_token = os.environ["MYSQL_TPCC_TYPED_CAPTURE_TOKEN"]
    if (
        not isinstance(header, dict)
        or header.get("record_type") != "header"
        or header.get("schema_version") != TYPED_TRACE_SCHEMA
        or header.get("manifest_sha256") != expected_manifest
        or header.get("capture_token") != expected_token
        or header.get("capture_only") is not True
        or header.get("outer_execution_authorized") is not False
        or header.get("correctness_verified") is not True
        or header.get("qps_included") is not False
        or sensitive_sql.is_symlink()
        or not sensitive_sql.is_file()
        or sha256(sensitive_sql) != SENSITIVE_SQL_SHA256
        or consumer_authority.is_symlink()
        or not consumer_authority.is_file()
    ):
        raise RuntimeError(
            "TPC-C real SensitiveStockLevel typed join authority is absent"
        )
    authority = trace_path.with_name(
        trace_path.name + ".sensitive_join_authority.json"
    )
    atomic_json(
        authority,
        {
            "schema_version":
                "mysql_tpcc_sensitive_stock_level_real_join_authority_v1",
            "status": "PASS_REAL_OWNER_TRACE_JOIN",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "capture_only": True,
            "normalized_transaction_type": "StockLevel",
            "binding_transaction_type": "SensitiveStockLevel",
            "binding_id":
                "34:SensitiveStockLevel:"
                "SensitiveStockLevel/item-in-orderline-w1",
            "trace_path": str(trace_path),
            "trace_header": header,
            "lock_path": str(lock_path),
            "sensitive_sql_path": str(sensitive_sql),
            "sensitive_sql_sha256": sha256(sensitive_sql),
            "consumer_authority_path": str(consumer_authority),
            "consumer_authority_sha256": sha256(consumer_authority),
            "qps_resource_input_prohibited": True,
            "synthetic_or_fabricated_join_prohibited": True,
            "outer_execution_authorized": False,
            "accepted_round_authorized": False,
        },
    )
    return authority


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--defaults-extra-file", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--sensitive-sql", type=Path, required=True)
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("BenchBase command is required after --")
    if args.interval_seconds != 15.0:
        parser.error("formal TPC-C sensitive interval must be exactly 15 seconds")
    sql = args.sensitive_sql.read_text(encoding="utf-8")
    args.event_path.parent.mkdir(parents=True, exist_ok=True)
    if args.event_path.exists() or args.event_path.is_symlink():
        raise SystemExit("stale TPC-C sensitive event artifact")

    stop = threading.Event()
    errors: list[str] = []
    typed_trace = bool(os.environ.get("MYSQL_TPCC_TYPED_TRACE_PATH"))
    trace_path = None
    consumer_authority = None
    join_authority = None
    if typed_trace:
        typed_required = (
            "MYSQL_TPCC_TYPED_BENCHBASE_CLASSPATH",
            "MYSQL_TPCC_TYPED_TARGET_JDBC_URL",
            "MYSQL_TPCC_TYPED_DEFAULTS_FILE",
            "MYSQL_TPCC_TYPED_SENSITIVE_SQL_PATH",
        )
        missing = [name for name in typed_required if not os.environ.get(name)]
        if missing:
            raise SystemExit(
                "incomplete TPC-C typed sensitive environment: "
                + ",".join(missing)
            )
        configured_sql = Path(
            os.environ["MYSQL_TPCC_TYPED_SENSITIVE_SQL_PATH"]
        ).resolve(strict=True)
        if configured_sql != args.sensitive_sql.resolve(strict=True):
            raise SystemExit("TPC-C typed sensitive SQL path differs")
        if os.environ.get("MYSQL_TPCC_TYPED_TRACE_ROLE") != "OWNER":
            raise SystemExit("BenchBase TPC-C typed trace role must be OWNER")
        trace_path = Path(
            os.environ["MYSQL_TPCC_TYPED_TRACE_PATH"]
        ).resolve()
        consumer_authority = enable_typed_consumers_before_workload(
            args, trace_path
        )

    def sensitive_loop() -> None:
        nonlocal join_authority
        sequence = 0
        deadline = time.monotonic() + args.interval_seconds
        while not stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining > 0 and stop.wait(remaining):
                break
            sequence += 1
            final_row = None
            for attempt in range(1, 4):
                begun = time.time_ns()
                try:
                    if typed_trace:
                        assert trace_path is not None
                        assert consumer_authority is not None
                        if join_authority is None:
                            join_authority = wait_for_real_typed_join_authority(
                                trace_path,
                                configured_sql,
                                consumer_authority,
                            )
                        helper_environment = os.environ.copy()
                        helper_environment["MYSQL_TPCC_TYPED_TRACE_ROLE"] = "JOINER"
                        helper_environment[
                            "MYSQL_TPCC_TYPED_JOIN_AUTHORITY_PATH"
                        ] = str(join_authority)
                        helper_environment[
                            "MYSQL_TPCC_TYPED_JOIN_AUTHORITY_SHA256"
                        ] = sha256(join_authority)
                        helper_command = [
                            os.environ.get("MYSQL_TPCC_TYPED_JAVA", "java"),
                            "-cp",
                            os.environ["MYSQL_TPCC_TYPED_BENCHBASE_CLASSPATH"],
                            "com.oltpbenchmark.TpccSensitiveTypedMain",
                        ]
                    else:
                        helper_environment = None
                        helper_command = [
                            "mysql", f"--defaults-file={args.defaults_extra_file}",
                            "--protocol=tcp", f"--host={args.host}",
                            f"--port={args.port}", "--user=root",
                            "--connect-timeout=30",
                            "--ssl-mode=DISABLED", "--get-server-public-key",
                            "--batch", "--raw",
                            "--skip-column-names", args.database, "-e", sql,
                        ]
                    result = subprocess.run(
                        helper_command,
                        env=helper_environment,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=120,
                        check=False,
                    )
                    returncode = result.returncode
                    error = (
                        None if returncode == 0 else (result.stderr or "")[-500:]
                    )
                except subprocess.TimeoutExpired:
                    returncode = 124
                    error = "sensitive SQL exceeded the formal 120 second timeout"
                ended = time.time_ns()
                row = {
                    "epoch_ms": ended // 1_000_000,
                    "latency_ms": (ended - begun) / 1_000_000.0,
                    "sequence": sequence,
                    "attempt": attempt,
                    "status": (
                        "complete"
                        if returncode == 0
                        else ("retry_error" if attempt < 3 else "error")
                    ),
                    "returncode": returncode,
                    "error": error,
                    "collector": (
                        "jdbc_typed_performance_schema"
                        if typed_trace
                        else "mysql_cli_completion_only"
                    ),
                }
                with args.event_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(row, sort_keys=True, separators=(",", ":"))
                        + "\n"
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
                final_row = row
                if returncode == 0:
                    break
                if stop.wait(1.0):
                    break
            assert final_row is not None
            if final_row["returncode"] != 0:
                errors.append(str(final_row["error"]))
                stop.set()
                break
            # Deadlines are anchored to the initial start, so query latency
            # cannot silently turn the 15-second cadence into 15+latency.
            deadline += args.interval_seconds

    worker = threading.Thread(target=sensitive_loop, name="tpcc-sensitive", daemon=True)
    worker.start()
    process = subprocess.Popen(command)
    try:
        while process.poll() is None and not errors:
            time.sleep(0.1)
        if errors and process.poll() is None:
            process.terminate()
        returncode = process.wait()
    finally:
        stop.set()
        worker.join(timeout=20)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
    if errors:
        print(f"TPC-C sensitive query failed: {errors[-1]}", file=sys.stderr)
        return 2
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
