"""Create and load the Join Order Benchmark data into an isolated MySQL schema."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
import subprocess
from typing import Dict, List


CREATE_TABLE = re.compile(r"CREATE TABLE\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\);", re.I | re.S)
COLUMN = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+", re.I)


def _mysql(args: argparse.Namespace, database: str | None, *, sql: str | None = None, stdin: str | None = None) -> None:
    command = [
        "mysql", "--local-infile=1", "--protocol=tcp", f"--host={args.host}",
        f"--port={args.port}", f"--user={args.user}", f"--password={args.password}",
        "--default-character-set=utf8mb4",
    ]
    if database:
        command.append(database)
    if sql is not None:
        command.extend(["-e", sql])
    subprocess.run(command, input=stdin, text=True, check=True)


def _tables(schema: str) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for match in CREATE_TABLE.finditer(schema):
        columns: List[str] = []
        for raw in match.group(2).splitlines():
            item = raw.strip().rstrip(",")
            if not item or item.upper().startswith(("PRIMARY ", "FOREIGN ", "UNIQUE ", "CONSTRAINT ")):
                continue
            parsed = COLUMN.match(item)
            if parsed:
                columns.append(parsed.group(1))
        result[match.group(1)] = columns
    return result


def _load_one(args: argparse.Namespace, table: str, columns: List[str]) -> str:
    source = (args.data_dir / f"{table}.csv").resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    variables = [f"@v{index}" for index in range(len(columns))]
    assignments = ", ".join(f"`{name}`=NULLIF({var},'')" for name, var in zip(columns, variables))
    statement = (
        f"SET SESSION sql_mode='NO_ENGINE_SUBSTITUTION'; "
        f"LOAD DATA LOCAL INFILE '{source}' INTO TABLE `{table}` "
        "CHARACTER SET utf8mb4 FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"' "
        "ESCAPED BY '\"' LINES TERMINATED BY '\\n' "
        f"({','.join(variables)}) SET {assignments};"
    )
    _mysql(args, args.database, sql=statement)
    return table


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default='CHANGE_ME')
    parser.add_argument("--database", default="job")
    parser.add_argument("--schema", type=Path, default=Path("/home/anonymous/dataset/pg_data/join-order-benchmark/schema.sql"))
    parser.add_argument("--indexes", type=Path, default=Path("/home/anonymous/dataset/pg_data/join-order-benchmark/fkindexes.sql"))
    parser.add_argument("--data-dir", type=Path, default=Path("/home/anonymous/dataset/pg_data/jobdata"))
    parser.add_argument("--load-workers", type=int, default=4)
    args = parser.parse_args()

    schema = args.schema.read_text(encoding="utf-8")
    table_columns = _tables(schema)
    if not table_columns:
        raise RuntimeError(f"no tables parsed from {args.schema}")
    _mysql(args, None, sql=f"CREATE DATABASE IF NOT EXISTS `{args.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    _mysql(args, args.database, stdin=schema)
    with ThreadPoolExecutor(max_workers=max(1, args.load_workers)) as pool:
        futures = {pool.submit(_load_one, args, table, columns): table for table, columns in table_columns.items()}
        for future in as_completed(futures):
            print(f"loaded {future.result()}", flush=True)
    _mysql(args, args.database, stdin=args.indexes.read_text(encoding="utf-8"))
    _mysql(args, args.database, sql="ANALYZE TABLE " + ",".join(f"`{name}`" for name in table_columns))
    print(f"JOB prepared: {len(table_columns)} tables", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
