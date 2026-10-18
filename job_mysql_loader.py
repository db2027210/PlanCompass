"""Generate and optionally run an isolated MySQL 8 loader for JOB IMDb CSVs.

The default action is dry-run: it only creates SQL and JSON files under a new,
explicit output directory.  It never starts/stops containers or database services.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Mapping, Sequence


DEFAULT_DATA = Path("/home/anonymous/dataset/pg_data/jobdata")
DEFAULT_SCHEMA = Path("/home/anonymous/dataset/pg_data/join-order-benchmark/schema.sql")
DEFAULT_INDEXES = Path("/home/anonymous/dataset/pg_data/join-order-benchmark/fkindexes.sql")


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    database: str
    user: str
    password: str = ""

    def __post_init__(self) -> None:
        if not self.host or not self.database or not self.user:
            raise ValueError("host, database and user must be explicit")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be in 1..65535")
        if not re.fullmatch(r"[A-Za-z0-9_$]+", self.database):
            raise ValueError("unsafe database name")


@dataclass(frozen=True)
class Column:
    name: str
    mysql_type: str
    nullable: bool


def _new_output(path: Path | str) -> Path:
    output = Path(path).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to reuse output-dir: {output}")
    output.mkdir(parents=True)
    return output


def _atomic_write(path: Path, value: str, mode: int = 0o644) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.chmod(mode)
    os.replace(temporary, path)


def parse_postgres_schema(text: str) -> dict[str, list[Column]]:
    tables: dict[str, list[Column]] = {}
    for match in re.finditer(r"(?is)CREATE\s+TABLE\s+(\w+)\s*\((.*?)\)\s*;", text):
        name, body = match.groups()
        columns: list[Column] = []
        for raw in body.splitlines():
            raw = raw.strip().rstrip(",")
            if not raw or raw.upper().startswith(("PRIMARY ", "FOREIGN ", "CONSTRAINT ")):
                continue
            item = re.match(r"(?is)^(\w+)\s+(.+?)(?:\s+NOT\s+NULL)?(?:\s+PRIMARY\s+KEY)?$", raw)
            if not item:
                raise ValueError(f"cannot parse schema line: {raw}")
            column, definition = item.groups()
            nullable = "NOT NULL" not in raw.upper() and "PRIMARY KEY" not in raw.upper()
            lowered = definition.lower()
            if lowered.startswith("integer"):
                mysql_type = "INT"
            elif lowered.startswith("text"):
                mysql_type = "LONGTEXT"
            else:
                varchar = re.match(r"character\s+varying\s*\((\d+)\)", lowered)
                if not varchar:
                    raise ValueError(f"unsupported PostgreSQL type: {definition}")
                mysql_type = f"VARCHAR({varchar.group(1)})"
            columns.append(Column(column, mysql_type, nullable))
        if not columns:
            raise ValueError(f"table {name} has no columns")
        tables[name] = columns
    if not tables:
        raise ValueError("no CREATE TABLE statements found")
    return tables


def mysql_schema_sql(tables: Mapping[str, Sequence[Column]]) -> str:
    chunks = ["SET NAMES utf8mb4;", "SET FOREIGN_KEY_CHECKS=0;"]
    for table, columns in tables.items():
        definitions = []
        for column in columns:
            nullability = "NULL" if column.nullable else "NOT NULL"
            definitions.append(f"  `{column.name}` {column.mysql_type} {nullability}")
        definitions.append("  PRIMARY KEY (`id`)")
        chunks.append(f"CREATE TABLE `{table}` (\n" + ",\n".join(definitions) +
                      "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;")
    return "\n\n".join(chunks) + "\n"


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def load_sql(tables: Mapping[str, Sequence[Column]], data_dir: Path) -> str:
    chunks = ["SET SESSION sql_mode='NO_ENGINE_SUBSTITUTION';",
              "SET SESSION unique_checks=0;", "SET SESSION foreign_key_checks=0;"]
    for table, columns in tables.items():
        path = (data_dir / f"{table}.csv").resolve()
        variables = ", ".join(f"@c{i}" for i in range(len(columns)))
        assignments = []
        for index, column in enumerate(columns):
            value = f"@c{index}"
            # PostgreSQL COPY CSV represents NULL as an unquoted empty field.  The
            # IMDb export has no semantically significant quoted empty strings.
            expression = f"NULLIF({value}, '')" if column.nullable else value
            assignments.append(f"`{column.name}`={expression}")
        chunks.append(
            f"LOAD DATA LOCAL INFILE {_sql_string(str(path))} INTO TABLE `{table}`\n"
            "CHARACTER SET utf8mb4\n"
            "FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"' ESCAPED BY '\\\\'\n"
            "LINES TERMINATED BY '\\n'\n"
            f"({variables}) SET " + ", ".join(assignments) + ";"
        )
    return "\n\n".join(chunks) + "\n"


def validation_sql(tables: Mapping[str, Sequence[Column]]) -> str:
    counts = " UNION ALL\n".join(
        f"SELECT '{table}' AS table_name, COUNT(*) AS row_count FROM `{table}`"
        for table in tables
    ) + ";"
    return (
        counts + "\n\n"
        "SELECT table_name, engine, table_rows, data_length, index_length\n"
        "FROM information_schema.tables\n"
        "WHERE table_schema=DATABASE() ORDER BY table_name;\n\n"
        "SELECT table_name, column_name, data_type, is_nullable\n"
        "FROM information_schema.columns\n"
        "WHERE table_schema=DATABASE() ORDER BY table_name, ordinal_position;\n"
    )


def _csv_probe(path: Path, expected_columns: int) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        # These files came from PostgreSQL COPY CSV and contain backslash-escaped
        # quotes in free text (notably person_info).
        first = next(csv.reader(handle, escapechar="\\"), None)
    if first is None or len(first) != expected_columns:
        raise ValueError(f"{path}: first CSV record has {len(first or [])}, expected {expected_columns} columns")
    return {"path": str(path.resolve()), "bytes": path.stat().st_size,
            "expected_columns": expected_columns}


def prepare(
    endpoint: Endpoint, output_dir: Path | str, *, data_dir: Path | str = DEFAULT_DATA,
    schema_path: Path | str = DEFAULT_SCHEMA, indexes_path: Path | str = DEFAULT_INDEXES,
) -> dict[str, Any]:
    output = _new_output(output_dir)
    data = Path(data_dir).resolve()
    tables = parse_postgres_schema(Path(schema_path).read_text(encoding="utf-8"))
    csv_files = [_csv_probe(data / f"{table}.csv", len(columns))
                 for table, columns in tables.items()]
    schema_out, load_out, indexes_out, validate_out = (
        output / "01_schema.sql", output / "02_load.sql",
        output / "03_indexes.sql", output / "04_validate.sql")
    _atomic_write(schema_out, mysql_schema_sql(tables))
    _atomic_write(load_out, load_sql(tables, data))
    index_text = Path(indexes_path).read_text(encoding="utf-8")
    _atomic_write(indexes_out, index_text.rstrip() + "\n")
    _atomic_write(validate_out, validation_sql(tables))
    total_bytes = sum(item["bytes"] for item in csv_files)
    manifest = {
        "mode": "dry_run_only_no_database_connection", "endpoint": {
            "host": endpoint.host, "port": endpoint.port,
            "database": endpoint.database, "user": endpoint.user,
        },
        "output_dir": str(output), "table_count": len(tables), "csv_total_bytes": total_bytes,
        "csv_files": csv_files,
        "sql": [str(schema_out), str(load_out), str(indexes_out), str(validate_out)],
        "estimated_database_space_bytes": [total_bytes * 2, total_bytes * 4],
        "estimated_load_minutes_nvme": [15, 45],
    }
    _atomic_write(output / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    return manifest


def execute_plan(
    endpoint: Endpoint, manifest_path: Path | str, *, mysql_binary: str = "mysql",
    timeout_s: int = 7200,
) -> dict[str, Any]:
    """Execute a previously generated plan; explicit call is the second opt-in."""
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest["endpoint"]
    actual = {"host": endpoint.host, "port": endpoint.port,
              "database": endpoint.database, "user": endpoint.user}
    if actual != expected:
        raise ValueError("endpoint does not match dry-run manifest")
    binary = shutil.which(mysql_binary)
    if not binary:
        raise FileNotFoundError(mysql_binary)
    argv = [binary, "--local-infile=1", "--batch", "-h", endpoint.host, "-P", str(endpoint.port),
            "-u", endpoint.user, endpoint.database]
    env = dict(os.environ)
    if endpoint.password:
        env["MYSQL_PWD"] = endpoint.password
    results = []
    started = time.monotonic()
    for sql_path in manifest["sql"]:
        sql_file = Path(sql_path)
        completed = subprocess.run(argv, input=sql_file.read_text(encoding="utf-8"), text=True,
                                   capture_output=True, env=env, timeout=timeout_s, check=False)
        log = manifest_path.parent / f"{sql_file.stem}.stdout.log"
        _atomic_write(log, completed.stdout or "")
        _atomic_write(log.with_name(f"{sql_file.stem}.stderr.log"), completed.stderr or "")
        results.append({"sql": str(sql_file), "returncode": completed.returncode})
        if completed.returncode:
            raise RuntimeError(f"mysql failed for {sql_file}; see {log.parent}")
    result = {"status": "completed", "wall_time_s": time.monotonic() - started, "steps": results}
    _atomic_write(manifest_path.parent / "execution_result.json", json.dumps(result, indent=2) + "\n")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True); parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True); parser.add_argument("--user", required=True)
    parser.add_argument("--password", default=""); parser.add_argument("--output-dir", required=True)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA)); parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    endpoint = Endpoint(args.host, args.port, args.database, args.user, args.password)
    manifest = prepare(endpoint, args.output_dir, data_dir=args.data_dir)
    print(json.dumps(manifest, indent=2))
    if args.execute:
        execute_plan(endpoint, Path(args.output_dir) / "manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
