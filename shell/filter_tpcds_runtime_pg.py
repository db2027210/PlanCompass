#!/usr/bin/env python3
"""Filter PostgreSQL TPC-DS SQL files by real execution timeout."""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Tuple


def run_sql(
    sql_file: Path,
    *,
    host: str,
    port: str,
    user: str,
    dbname: str,
    password: str,
    statement_timeout_ms: int,
    process_timeout_sec: int,
) -> Tuple[bool, float, str]:
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [
                "psql",
                "-h",
                host,
                "-p",
                str(port),
                "-U",
                user,
                "-d",
                dbname,
                "-v",
                "ON_ERROR_STOP=1",
                "-q",
                "-X",
                "-c",
                f"SET statement_timeout = {int(statement_timeout_ms)};",
                "-f",
                str(sql_file),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=process_timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - start
        detail = ""
        if exc.stderr:
            detail = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr)
        return False, elapsed, f"process timeout after {process_timeout_sec}s. {detail}".strip()

    elapsed = time.perf_counter() - start
    if proc.returncode == 0:
        return True, elapsed, ""
    return False, elapsed, (proc.stderr or proc.stdout or "").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--host", default="192.0.2.8")
    parser.add_argument("--port", default="25432")
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="tpcds")
    parser.add_argument("--password", default=os.environ.get("PGPASSWORD", ""))
    parser.add_argument("--statement-timeout-ms", type=int, default=60000)
    parser.add_argument("--process-timeout-sec", type=int, default=75)
    parser.add_argument("--max-files", type=int, default=0, help="0 means all files")
    args = parser.parse_args()

    files = sorted(args.input_dir.glob("*.sql"))
    if args.max_files > 0:
        files = files[: args.max_files]
    if not files:
        raise SystemExit(f"no .sql files found in {args.input_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    reject_dir = args.output_dir.parent / f"{args.output_dir.name}_runtime_rejected"
    reject_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir.parent / f"{args.output_dir.name}_runtime_report.csv"

    passed = 0
    failed = 0
    with report_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["file", "passed", "elapsed_sec", "error"])
        writer.writeheader()
        for idx, sql_file in enumerate(files, 1):
            print(f"[{idx}/{len(files)}] running {sql_file.name}", flush=True)
            ok, elapsed, error = run_sql(
                sql_file,
                host=args.host,
                port=args.port,
                user=args.user,
                dbname=args.dbname,
                password=args.password,
                statement_timeout_ms=args.statement_timeout_ms,
                process_timeout_sec=args.process_timeout_sec,
            )
            if ok:
                shutil.copy2(sql_file, args.output_dir / sql_file.name)
                passed += 1
            else:
                shutil.copy2(sql_file, reject_dir / sql_file.name)
                failed += 1
            writer.writerow(
                {
                    "file": sql_file.name,
                    "passed": ok,
                    "elapsed_sec": f"{elapsed:.6f}",
                    "error": error.replace("\n", " ")[:1000],
                }
            )
            fh.flush()

    print(f"input={len(files)}", flush=True)
    print(f"passed={passed}", flush=True)
    print(f"failed={failed}", flush=True)
    print(f"output_dir={args.output_dir}", flush=True)
    print(f"reject_dir={reject_dir}", flush=True)
    print(f"report={report_path}", flush=True)


if __name__ == "__main__":
    main()
