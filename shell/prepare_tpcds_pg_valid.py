#!/usr/bin/env python3
"""Prepare a PostgreSQL-valid TPC-DS query subset from split dsqgen SQL files."""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple


TOP_RE = re.compile(r"\bselect\s+top\s+(?P<n>\d+)\s+", re.IGNORECASE)
DAYS_RE = re.compile(r"\+\s+(?P<n>\d+)\s+days\b", re.IGNORECASE)
LOCHIERARCHY_RE = re.compile(
    r"grouping\((?P<a>[^)]+)\)\s*\+\s*grouping\((?P<b>[^)]+)\)\s+as\s+lochierarchy",
    re.IGNORECASE,
)


def _strip_top(sql: str) -> str:
    # The splitter already converts the leading TOP to LIMIT. Remaining TOP
    # occurrences are usually nested TPC-DS presentation limits; dropping them
    # is preferable to keeping SQL Server syntax in PostgreSQL validation.
    return TOP_RE.sub("select ", sql)


def _fix_days(sql: str) -> str:
    return DAYS_RE.sub(lambda m: f"+ interval '{m.group('n')} days'", sql)


def _fix_lochierarchy_alias(sql: str) -> str:
    match = LOCHIERARCHY_RE.search(sql)
    if not match:
        return sql
    expr = f"grouping({match.group('a')})+grouping({match.group('b')})"
    return re.sub(r"\blochierarchy\b", expr, sql, flags=re.IGNORECASE)


def normalize_sql(sql: str) -> str:
    sql = _strip_top(sql)
    sql = _fix_days(sql)
    sql = _fix_lochierarchy_alias(sql)
    return sql.strip() + "\n"


def psql_explain(
    sql: str,
    *,
    host: str,
    port: str,
    user: str,
    dbname: str,
    password: str,
    timeout_sec: int,
) -> Tuple[bool, str]:
    statements = [
        statement.strip()
        for statement in sql.split(";")
        if statement.strip()
    ]
    wrapped_sql = "\n".join(
        f"EXPLAIN {statement};"
        for statement in statements
    )
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
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
                wrapped_sql,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        detail = ""
        if exc.stderr:
            detail = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr)
        return False, f"EXPLAIN timeout after {timeout_sec}s. {detail}".strip()
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout or "").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--host", default="192.0.2.8")
    parser.add_argument("--port", default="25432")
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="tpcds")
    parser.add_argument("--password", default=os.environ.get("PGPASSWORD", ""))
    parser.add_argument("--timeout-sec", type=int, default=30)
    parser.add_argument("--min-valid", type=int, default=1)
    args = parser.parse_args()

    input_files = sorted(args.input_dir.glob("*.sql"))
    if not input_files:
        raise SystemExit(f"no .sql files found in {args.input_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    reject_dir = args.output_dir.parent / f"{args.output_dir.name}_rejected"
    reject_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir.parent / f"{args.output_dir.name}_report.csv"

    valid_count = 0
    rejected_count = 0
    rows: List[dict] = []

    for source in input_files:
        raw_sql = source.read_text(encoding="utf-8")
        normalized = normalize_sql(raw_sql)
        ok, error = psql_explain(
            normalized,
            host=args.host,
            port=args.port,
            user=args.user,
            dbname=args.dbname,
            password=args.password,
            timeout_sec=args.timeout_sec,
        )
        if ok:
            (args.output_dir / source.name).write_text(normalized, encoding="utf-8")
            valid_count += 1
        else:
            shutil.copy2(source, reject_dir / source.name)
            rejected_count += 1
        rows.append(
            {
                "file": source.name,
                "valid": ok,
                "error": error.replace("\n", " ")[:1000],
            }
        )

    with report_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["file", "valid", "error"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"input={len(input_files)}", flush=True)
    print(f"valid={valid_count}", flush=True)
    print(f"rejected={rejected_count}", flush=True)
    print(f"output_dir={args.output_dir}", flush=True)
    print(f"reject_dir={reject_dir}", flush=True)
    print(f"report={report_path}", flush=True)

    if valid_count < args.min_valid:
        raise SystemExit(f"valid query count {valid_count} < min-valid {args.min_valid}")


if __name__ == "__main__":
    main()
