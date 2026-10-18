#!/usr/bin/env python3
"""Copy PostgreSQL planner statistics between equivalent worker databases.

The destination data and schema must already be an exact logical clone of the
source.  Relation OIDs are mapped by schema/name before pg_statistic rows are
installed.  The command is fail-closed and requires ``--apply`` because it
mutates only the destination database's planner catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Mapping, Tuple


INSERT_RE = re.compile(
    r"^(INSERT INTO pg_catalog\.pg_statistic .* VALUES \()(.*)(\);)$"
)


def pg_env(password: str) -> Dict[str, str]:
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    env["PGCONNECT_TIMEOUT"] = "5"
    return env


def psql(
    *,
    host: str,
    port: int,
    user: str,
    database: str,
    password: str,
    sql: str,
) -> str:
    proc = subprocess.run(
        [
            "psql",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            host,
            "-p",
            str(port),
            "-U",
            user,
            "-d",
            database,
            "-At",
            "-F",
            "\t",
            "-c",
            sql,
        ],
        env=pg_env(password),
        text=True,
        capture_output=True,
        check=False,
        timeout=300.0,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout.strip()


def relation_oids(connection: Mapping[str, object]) -> Dict[Tuple[str, str], int]:
    rows = psql(
        **connection,
        sql=(
            "SELECT n.nspname, c.relname, c.oid "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
            "ORDER BY n.nspname, c.relname;"
        ),
    )
    result: Dict[Tuple[str, str], int] = {}
    for row in rows.splitlines():
        if not row:
            continue
        schema, relation, oid_text = row.split("\t")
        key = (schema, relation)
        if key in result:
            raise RuntimeError(f"duplicate relation identity: {key}")
        result[key] = int(oid_text)
    return result


def relation_planner_statistics(
    connection: Mapping[str, object],
) -> Dict[Tuple[str, str], Tuple[int, float, int]]:
    rows = psql(
        **connection,
        sql=(
            "SELECT n.nspname, c.relname, c.relpages, c.reltuples::float8, "
            "c.relallvisible "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
            "ORDER BY n.nspname, c.relname;"
        ),
    )
    result: Dict[Tuple[str, str], Tuple[int, float, int]] = {}
    for row in rows.splitlines():
        if not row:
            continue
        schema, relation, pages, tuples, all_visible = row.split("\t")
        result[(schema, relation)] = (
            int(pages),
            float(tuples),
            int(all_visible),
        )
    return result


def database_identity(connection: Mapping[str, object]) -> str:
    return psql(
        **connection,
        sql=(
            "SELECT current_setting('server_version_num'), "
            "pg_encoding_to_char(d.encoding), d.datcollate, d.datctype "
            "FROM pg_database d WHERE d.datname = current_database();"
        ),
    )


def attribute_types(
    connection: Mapping[str, object],
) -> Dict[Tuple[int, int], Tuple[int, int]]:
    rows = psql(
        **connection,
        sql=(
            "SELECT c.oid, a.attnum, a.atttypid, a.atttypmod "
            "FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid "
            "WHERE a.attnum > 0 AND NOT a.attisdropped "
            "ORDER BY c.oid, a.attnum;"
        ),
    )
    result: Dict[Tuple[int, int], Tuple[int, int]] = {}
    for row in rows.splitlines():
        if not row:
            continue
        oid_text, attnum_text, type_oid_text, typmod_text = row.split("\t", 3)
        result[(int(oid_text), int(attnum_text))] = (
            int(type_oid_text),
            int(typmod_text),
        )
    return result


def dump_statistics(connection: Mapping[str, object]) -> str:
    proc = subprocess.run(
        [
            "pg_dump",
            "-h",
            str(connection["host"]),
            "-p",
            str(connection["port"]),
            "-U",
            str(connection["user"]),
            "-d",
            str(connection["database"]),
            "--data-only",
            "--table=pg_catalog.pg_statistic",
            "--column-inserts",
            "--rows-per-insert=1",
        ],
        env=pg_env(str(connection["password"])),
        text=True,
        capture_output=True,
        check=False,
        timeout=300.0,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout


def split_sql_values(value: str) -> List[str]:
    """Split one VALUES body while preserving quoted array literals."""
    result: List[str] = []
    start = 0
    in_quote = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'":
            if in_quote and index + 1 < len(value) and value[index + 1] == "'":
                index += 2
                continue
            in_quote = not in_quote
        elif char == "," and not in_quote:
            result.append(value[start:index].strip())
            start = index + 1
        index += 1
    result.append(value[start:].strip())
    if in_quote:
        raise RuntimeError("unterminated SQL literal in pg_statistic dump")
    return result


def remap_statistics_sql(
    dump_sql: str,
    source_oids: Mapping[Tuple[str, str], int],
    destination_oids: Mapping[Tuple[str, str], int],
    source_attribute_types: Mapping[Tuple[int, int], Tuple[int, int]],
    source_relation_statistics: Mapping[
        Tuple[str, str], Tuple[int, float, int]
    ],
) -> Tuple[str, int]:
    if set(source_oids) != set(destination_oids):
        missing_destination = sorted(set(source_oids) - set(destination_oids))
        missing_source = sorted(set(destination_oids) - set(source_oids))
        raise RuntimeError(
            "source/destination relation sets differ: "
            f"missing_destination={missing_destination} missing_source={missing_source}"
        )
    oid_map = {
        source_oids[key]: destination_oids[key]
        for key in source_oids
    }
    destination_relation_oids = sorted(set(oid_map.values()))
    statements: List[str] = [
        "BEGIN;",
        (
            "DELETE FROM pg_catalog.pg_statistic WHERE starelid IN ("
            + ", ".join(str(value) for value in destination_relation_oids)
            + ");"
        ),
    ]
    for key in sorted(source_oids):
        pages, tuples, all_visible = source_relation_statistics[key]
        statements.append(
            "UPDATE pg_catalog.pg_class SET "
            f"relpages = {pages}, reltuples = {tuples:.17g}, "
            f"relallvisible = {all_visible} "
            f"WHERE oid = {destination_oids[key]};"
        )
    insert_count = 0
    for line in dump_sql.splitlines():
        match = INSERT_RE.match(line)
        if match is None:
            continue
        values = split_sql_values(match.group(2))
        if len(values) != 31:
            raise RuntimeError(
                f"unexpected pg_statistic column count: {len(values)}"
            )
        source_oid = int(values[0])
        source_attnum = int(values[1])
        if source_oid not in oid_map:
            continue
        destination_oid = oid_map[source_oid]
        attribute_type = source_attribute_types.get((source_oid, source_attnum))
        if attribute_type is None:
            raise RuntimeError(
                "missing attribute type for pg_statistic row: "
                f"starelid={source_oid} staattnum={source_attnum}"
            )
        values[0] = str(destination_oid)
        element_type_oid, element_typmod = attribute_type
        for value_index in range(26, 31):
            if values[value_index].upper() != "NULL":
                values[value_index] = (
                    "pg_catalog.array_in("
                    f"{values[value_index]}::cstring, "
                    f"{element_type_oid}, {element_typmod})"
                )
        statements.append(
            match.group(1) + ", ".join(values) + match.group(3)
        )
        insert_count += 1
    if insert_count <= 0:
        raise RuntimeError("pg_statistic dump contained no INSERT statements")
    statements.extend(["COMMIT;", ""])
    return "\n".join(statements), insert_count


def apply_sql(connection: Mapping[str, object], sql: str) -> None:
    proc = subprocess.run(
        [
            "psql",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            str(connection["host"]),
            "-p",
            str(connection["port"]),
            "-U",
            str(connection["user"]),
            "-d",
            str(connection["database"]),
        ],
        env=pg_env(str(connection["password"])),
        input=sql,
        text=True,
        capture_output=True,
        check=False,
        timeout=300.0,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-host", default="127.0.0.1")
    parser.add_argument("--source-port", type=int, required=True)
    parser.add_argument("--destination-host", default="127.0.0.1")
    parser.add_argument("--destination-port", type=int, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default="postgres")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = {
        "host": args.source_host,
        "port": args.source_port,
        "user": args.user,
        "database": args.database,
        "password": args.password,
    }
    destination = {
        "host": args.destination_host,
        "port": args.destination_port,
        "user": args.user,
        "database": args.database,
        "password": args.password,
    }
    source_identity = database_identity(source)
    destination_identity = database_identity(destination)
    if source_identity != destination_identity:
        raise RuntimeError(
            "source/destination PostgreSQL identity differs: "
            f"source={source_identity!r} destination={destination_identity!r}"
        )
    source_oids = relation_oids(source)
    destination_oids = relation_oids(destination)
    source_attribute_types = attribute_types(source)
    source_relation_statistics = relation_planner_statistics(source)
    dump_sql = dump_statistics(source)
    remapped_sql, insert_count = remap_statistics_sql(
        dump_sql,
        source_oids,
        destination_oids,
        source_attribute_types,
        source_relation_statistics,
    )
    if not args.apply:
        raise RuntimeError("refusing to mutate destination without --apply")
    apply_sql(destination, remapped_sql)
    receipt = {
        "contract": "pg_planner_statistics_cross_worker_sync_v1",
        "source": {key: value for key, value in source.items() if key != "password"},
        "destination": {
            key: value for key, value in destination.items() if key != "password"
        },
        "database_identity": source_identity,
        "relation_count": len(source_oids),
        "statistics_insert_count": insert_count,
        "source_dump_sha256": hashlib.sha256(dump_sql.encode("utf-8")).hexdigest(),
        "remapped_sql_sha256": hashlib.sha256(remapped_sql.encode("utf-8")).hexdigest(),
        "applied": True,
        "completed_at_epoch_s": time.time(),
    }
    if args.receipt is not None:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
