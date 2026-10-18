#!/usr/bin/env python3
"""Split dsqgen output into PostgreSQL-friendly per-query SQL files."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


START_RE = re.compile(
    r"^-- start query (?P<query_no>\d+) in stream (?P<stream>\d+) using template (?P<template>\S+)\s*$",
    re.IGNORECASE,
)
TOP_RE = re.compile(r"\bselect\s+top\s+(?P<n>\d+)\s+", re.IGNORECASE)


def convert_top_to_limit(sql: str) -> str:
    """Convert TPC-DS 'select top N ...' to PostgreSQL 'select ... limit N'."""
    match = TOP_RE.search(sql)
    if not match:
        return sql

    limit_n = match.group("n")
    sql = sql[: match.start()] + "select " + sql[match.end() :]

    stripped = sql.rstrip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    return f"{stripped}\nlimit {limit_n};\n"


def split_queries(input_file: Path, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    current_header = None
    current_lines = []
    written = 0

    def flush() -> None:
        nonlocal written, current_header, current_lines
        if current_header is None or not current_lines:
            return
        query_no = int(current_header.group("query_no"))
        template = current_header.group("template").replace(".tpl", "")
        sql = "".join(current_lines).strip()
        sql = convert_top_to_limit(sql)
        if not sql.endswith("\n"):
            sql += "\n"
        out_file = output_dir / f"q{query_no:03d}_{template}.sql"
        out_file.write_text(sql, encoding="utf-8")
        written += 1

    with input_file.open("r", encoding="utf-8") as fh:
        for line in fh:
            start_match = START_RE.match(line)
            if start_match:
                flush()
                current_header = start_match
                current_lines = []
                continue
            if line.lower().startswith("-- end query "):
                flush()
                current_header = None
                current_lines = []
                continue
            if current_header is not None:
                current_lines.append(line)

    flush()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="dsqgen query_*.sql file")
    parser.add_argument("--output-dir", required=True, type=Path, help="directory for split .sql files")
    args = parser.parse_args()

    count = split_queries(args.input, args.output_dir)
    print(f"split_queries={count}", flush=True)
    print(f"output_dir={args.output_dir}", flush=True)


if __name__ == "__main__":
    main()
