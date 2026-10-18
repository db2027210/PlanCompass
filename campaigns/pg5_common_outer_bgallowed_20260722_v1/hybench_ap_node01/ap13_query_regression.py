#!/usr/bin/env python3
"""Run each HyBench AP1-AP13 query in isolation through the private JDBC patch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import tomllib
from typing import Any, Dict


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def replace_properties(payload: str, replacements: Dict[str, str]) -> str:
    output = []
    replaced = set()
    for line in payload.splitlines():
        key = line.split("=", 1)[0] if "=" in line else None
        if key in replacements:
            output.append(f"{key}={replacements[key]}")
            replaced.add(key)
        else:
            output.append(line)
    missing = set(replacements) - replaced
    if missing:
        raise RuntimeError(f"base properties missing keys: {sorted(missing)}")
    return "\n".join(output) + "\n"


def parse_jsonl(path: Path) -> list[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def terminate_owned_process(process: subprocess.Popen[str]) -> int:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            return int(process.wait(timeout=10))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
    return int(process.wait(timeout=10))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-properties", required=True, type=Path)
    parser.add_argument("--statements", required=True, type=Path)
    parser.add_argument("--fixed-driver-jar", required=True, type=Path)
    parser.add_argument("--fixed-driver-jar-sha256", required=True)
    parser.add_argument("--postgres-jdbc-jar", required=True, type=Path)
    parser.add_argument("--formal-jdbc-jar", required=True, type=Path)
    parser.add_argument("--hybench-jar", required=True, type=Path)
    parser.add_argument("--hybench-root", required=True, type=Path)
    parser.add_argument("--java", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    if sha256_file(args.fixed_driver_jar) != args.fixed_driver_jar_sha256:
        raise RuntimeError("private fixed JDBC JAR hash mismatch")
    args.output.mkdir(parents=True, exist_ok=False)
    statements = tomllib.loads(args.statements.read_text(encoding="utf-8"))
    base_payload = args.base_properties.read_text(encoding="utf-8")
    classpath = ":".join(
        str(path)
        for path in (
            args.fixed_driver_jar,
            args.postgres_jdbc_jar,
            args.formal_jdbc_jar,
            args.hybench_jar,
        )
    )
    results = []

    for query_id in range(1, 14):
        query_key = f"AP-{query_id}"
        target_sql = str(statements[query_key]["sql"])
        target_sha256 = hashlib.sha256(target_sql.encode("utf-8")).hexdigest()
        auxiliary_sha256 = None
        if query_id == 2:
            auxiliary_sql = str(statements["AP-2"]["1"]["sql"])
            auxiliary_sha256 = hashlib.sha256(auxiliary_sql.encode("utf-8")).hexdigest()

        query_dir = args.output / f"AP{query_id:02d}"
        query_dir.mkdir()
        props_path = query_dir / "hybench.props"
        props_path.write_text(
            replace_properties(
                base_payload,
                {
                    "tpclient": "0",
                    "apclient": "1",
                    "xtpclient": "0",
                    "xapclient": "1",
                    "ap_query_ids": str(query_id),
                    "ap_query_weights": "100",
                    "apRunMins": "2",
                    "apRunSecs": "120",
                },
            ),
            encoding="utf-8",
        )
        os.chmod(props_path, 0o600)
        query_log = query_dir / "queries.jsonl"
        transaction_log = query_dir / "transactions.jsonl"
        stdout_path = query_dir / "hybench.log"
        command = [
            str(args.java),
            "-cp",
            classpath,
            "com.hybench.HyBench",
            "-t",
            "runap",
            "-c",
            str(props_path),
            "-f",
            str(args.statements),
        ]
        environment = dict(os.environ)
        environment["FORMAL_PG_RESULT_LOG"] = str(query_log)
        environment["FORMAL_PG_TXN_LOG"] = str(transaction_log)
        started = time.monotonic()
        target_seen = False
        auxiliary_seen = auxiliary_sha256 is None
        process_exit_before_success = False
        with stdout_path.open("x", encoding="utf-8") as stdout:
            process = subprocess.Popen(
                command,
                cwd=args.hybench_root,
                env=environment,
                stdout=stdout,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            while time.monotonic() - started < args.timeout_seconds:
                rows = parse_jsonl(query_log)
                target_seen = any(
                    row.get("sql_sha256") == target_sha256 and row.get("success") is True
                    for row in rows
                )
                auxiliary_seen = auxiliary_sha256 is None or any(
                    row.get("sql_sha256") == auxiliary_sha256 and row.get("success") is True
                    for row in rows
                )
                if target_seen and auxiliary_seen:
                    break
                if process.poll() is not None:
                    process_exit_before_success = True
                    break
                time.sleep(0.5)
            raw_returncode = terminate_owned_process(process)
            stdout.flush()
            os.fsync(stdout.fileno())

        rows = parse_jsonl(query_log)
        failed_evidence = [row for row in rows if row.get("success") is not True]
        stdout_text = stdout_path.read_text(encoding="utf-8")
        forbidden_log_markers = [
            marker
            for marker in ("PSQLException", "SQLException", "Exception in thread")
            if marker in stdout_text
        ]
        passed = bool(
            target_seen
            and auxiliary_seen
            and not process_exit_before_success
            and not failed_evidence
            and not forbidden_log_markers
        )
        result = {
            "query_id": query_id,
            "passed": passed,
            "target_sql_sha256": target_sha256,
            "target_success_seen": target_seen,
            "auxiliary_sql_sha256": auxiliary_sha256,
            "auxiliary_success_seen": auxiliary_seen,
            "successful_evidence_records": sum(
                1 for row in rows if row.get("success") is True
            ),
            "failed_evidence_records": len(failed_evidence),
            "forbidden_log_markers": forbidden_log_markers,
            "process_exit_before_success": process_exit_before_success,
            "controller_terminated_after_success": target_seen and auxiliary_seen,
            "raw_process_returncode": raw_returncode,
            "wall_seconds": time.monotonic() - started,
            "properties_sha256": sha256_file(props_path),
            "query_log_sha256": sha256_file(query_log) if query_log.is_file() else None,
            "stdout_sha256": sha256_file(stdout_path),
        }
        write_json(query_dir / "result.json", result)
        results.append(result)
        write_json(
            args.output / "summary.json",
            {
                "schema_version": 1,
                "status": "running" if passed else "failed",
                "fixed_driver_jar": str(args.fixed_driver_jar),
                "fixed_driver_jar_sha256": args.fixed_driver_jar_sha256,
                "completed_query_ids": [row["query_id"] for row in results if row["passed"]],
                "results": results,
            },
        )
        print(json.dumps(result, sort_keys=True), flush=True)
        if not passed:
            return 1

    summary = {
        "schema_version": 1,
        "status": "passed",
        "requirement": "AP1-AP13 each completed successfully at least once in isolation",
        "fixed_driver_source_sha256": (
            "d1c1f7ba6edcfd31bd5132aa5c94a7b38e2850435338d01cb6ab30137d0b5109"
        ),
        "fixed_driver_jar": str(args.fixed_driver_jar),
        "fixed_driver_jar_sha256": args.fixed_driver_jar_sha256,
        "query_ids": list(range(1, 14)),
        "all_passed": True,
        "results": results,
    }
    write_json(args.output / "summary.json", summary)
    print(json.dumps({"status": "passed", "query_ids": list(range(1, 14))}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
