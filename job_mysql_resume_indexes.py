"""Resume missing JOB indexes, grouping all additions for each table in one scan."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import subprocess
import time


INDEX_RE = re.compile(
    r"(?i)^\s*create\s+index\s+(\w+)\s+on\s+(\w+)\s*\(([^)]+)\)\s*;\s*$"
)


def parse_indexes(path: Path) -> list[tuple[str, str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        match = INDEX_RE.match(line)
        if not match:
            raise ValueError(f"unsupported index statement: {line}")
        rows.append(match.groups())
    return rows


def mysql(args: argparse.Namespace, sql: str, timeout: int) -> str:
    env = dict(os.environ)
    env["MYSQL_PWD"] = args.password
    completed = subprocess.run(
        ["mysql", "--connect-timeout=5", "--batch", "--raw", "--skip-column-names",
         "-h", args.host, "-P", str(args.port), "-u", args.user, args.database, "-e", sql],
        text=True, capture_output=True, env=env, timeout=timeout, check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout


def build_missing(args: argparse.Namespace) -> dict[str, list[tuple[str, str]]]:
    desired = parse_indexes(args.indexes)
    existing_text = mysql(
        args,
        "SELECT index_name FROM information_schema.statistics "
        f"WHERE table_schema='{args.database}' GROUP BY index_name",
        30,
    )
    existing = {line.strip() for line in existing_text.splitlines() if line.strip()}
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for index, table, columns in desired:
        if index not in existing:
            grouped[table].append((index, columns))
    return dict(grouped)


def execute(args: argparse.Namespace) -> dict:
    grouped = build_missing(args)
    result = {"status": "running", "missing_by_table": grouped, "completed_tables": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    if not args.execute:
        result["status"] = "dry_run"
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        return result
    started = time.monotonic()
    for table, indexes in grouped.items():
        additions = ", ".join(f"ADD INDEX `{name}` ({columns})" for name, columns in indexes)
        mysql(args, f"ALTER TABLE `{table}` {additions}", args.timeout)
        result["completed_tables"].append(table)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    remaining = build_missing(args)
    result.update({"status": "completed" if not remaining else "incomplete",
                   "remaining_by_table": remaining,
                   "wall_time_s": time.monotonic() - started})
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True); parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True); parser.add_argument("--user", required=True)
    parser.add_argument("--password", default="")
    parser.add_argument("--indexes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=14400)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(execute(args), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
